# vim: set sw=4 ts=4 softtabstop=4 expandtab:
from . BackendBase import *
import logging
import os
import pprint
import time
import psutil
import threading
import traceback
import requests.exceptions
import json
_logger = logging.getLogger(__name__)


class DockerBackendException(BackendException):
    pass

try:
    import docker
except ImportError:
    raise DockerBackendException(
        'Could not import docker module from docker-py')

# Pool of Docker clients.
# It exists to avoid file exhaustion created by having
# too many clients open.
class DockerClientPool:
    def __init__(self, use_thread_id):
        assert isinstance(use_thread_id, bool)
        self._thread_client_map = dict()
        self.lock = threading.Lock()
        self._identifier_is_thread_id = use_thread_id
        self._identifier_counter = 0

    def _get_caller_ident(self):
        # Assume we already hold the lock as this in an
        # internal function.
        if self._identifier_is_thread_id:
            return threading.get_ident()
        else:
            # Use incrementing counter so that a client is
            # never re-used. This is the opposite of a "pool"
            # but this provides a way of getting to the runner's
            # old behaviour. We should remove this once we are
            # happy with this implementation.
            id = self._identifier_counter
            self._identifier_counter += 1
            return id

    def get_client(self):
        with self.lock:
            id = self._get_caller_ident()
            if id in self._thread_client_map:
                _logger.debug(
                    'Returning old Docker client for id: {}'.format(id))
                return self._thread_client_map[id]

            _logger.debug(
                'Creating new Docker client for id: {}'.format(id))
            new_client = docker.APIClient()
            self._thread_client_map[id] = new_client
            return new_client

    def release_client(self):
        with self.lock:
            id = self._get_caller_ident()
            if id not in self._thread_client_map:
                _logger.warning('Release called on non existing client')
                return False

            _logger.debug('Releasing Docker client for id: {}'.format(id))
            client = self._thread_client_map[id]
            client.close()
            self._thread_client_map.pop(id)
            return True

# HACK: Having a module global sucks. Can we stick this in some sort
# of context?
# This provides a pool of clients based of thread id. This aims
# to avoid exhaustion of file descriptors by capping the number
# of open clients.
_globalDockerClientPool = DockerClientPool(use_thread_id=True)

class DockerBackend(BackendBaseClass):

    def __init__(self, hostProgramPath, workingDirectory, timeLimit, memoryLimit, stackLimit, **kwargs):
        super().__init__(hostProgramPath, workingDirectory,
                         timeLimit, memoryLimit, stackLimit, **kwargs)
        self._container = None
        self._workDirInsideContainer = '/mnt/'
        self._skipToolExistsCheck = False
        self._userToUseInsideContainer = None
        self._dockerStatsOnExitShimBinary = None
        self._killLock = threading.Lock()
        self._additionalHostContainerFileMaps = dict()
        self._usedFileMapNames = set()  # HACK
        self._extra_volume_mounts = dict()
        # handle required options
        if not 'image' in kwargs:
            raise DockerBackendException('"image" but be specified')
        self._dockerImageName = kwargs['image']
        if not (isinstance(self._dockerImageName, str) and len(self._dockerImageName) > 0):
            raise DockerBackendException('"image" must to a non empty string')

        # Pretend user default is $USER
        if not 'user' in kwargs:
            kwargs['user'] = '$HOST_USER'

        requiredOptions = ['image']
        # handle other options
        for key, value in kwargs.items():
            if key in requiredOptions:
                continue
            if key == 'skip_tool_check':
                self._skipToolExistsCheck = value
                if not isinstance(self._skipToolExistsCheck, bool):
                    raise DockerBackendException(
                        '"skip_tool_check" must map to a bool')
                continue
            if key == 'image_work_dir':
                self._workDirInsideContainer = value
                if not (isinstance(self._workDirInsideContainer, str) and len(self._workDirInsideContainer) > 0):
                    raise DockerBackendException(
                        '"image_work_dir" must be a non empty string')
                if not os.path.isabs(value):
                    raise DockerBackendException(
                        '"image_work_dir" must be an absolute path')
                continue
            if key == 'user':
                if not (isinstance(value, str) or isinstance(value, int) or value == None):
                    raise DockerBackendException(
                        '"user" must be integer or a string')
                if value == None:
                    self._userToUseInsideContainer = None
                elif isinstance(value, int):
                    if value < 0:
                        raise DockerBackendException(
                            '"user" specified as an integer must be >= 0')
                    self._userToUseInsideContainer = value
                else:
                    # The choice of $ is deliberate because it is not a valid
                    # character in a username
                    if value == "$HOST_USER":
                        self._userToUseInsideContainer = "{}:{}".format(
                            os.getuid(), os.getgid())
                    else:
                        import re
                        if re.match(r'[a-z_][a-z0-9_-]*[$]?', value) == None:
                            raise DockerBackendException(
                                '"{}" is not a valid username'.format(value))
                        self._userToUseInsideContainer = value
                continue
            if key == 'docker_stats_on_exit_shim':
                if not isinstance(value, bool):
                    raise DockerBackendException(
                        '"docker_stats_on_exit_shim" should be a boolean')
                if value:
                    root = os.path.dirname(os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__))))
                    self._dockerStatsOnExitShimBinary = os.path.join(
                        root, 'external_deps', 'docker-stats-on-exit-shim')
                    _logger.info("Looking for '{}'".format(
                        self._dockerStatsOnExitShimBinary))
                    if not os.path.exists(self._dockerStatsOnExitShimBinary):
                        raise DockerBackendException(
                            "Could not find docker-stats-on-exit-shim at '{}'".format(self._dockerStatsOnExitShimBinary))
                continue
            if key == 'extra_mounts':
                if not isinstance(value, dict):
                    raise DockerBackendException(
                        '"extra_mounts" should be a dictionary')
                for host_path, props in value.items():
                    if not isinstance(host_path, str):
                        raise DockerBackendException(
                            '"extra_mounts" keys should be a string')
                    if not os.path.isabs(host_path):
                        raise DockerBackendException(
                            '"host_path" ("{}") must be an absolute path'.format(
                                host_path))
                    if not isinstance(props, dict):
                        raise DockerBackendException(
                            '"{}" should map to a dictionary'.format(
                                in_container_path))
                    in_container_path = None
                    read_only = True
                    try:
                        in_container_path =  props['container_path']
                    except KeyError:
                        raise DockerBackendException('"container_path" key is missing from {}'.format(props))
                    if 'read_only' in props:
                        read_only = props['read_only']
                    if not isinstance(read_only, bool):
                        raise DockerBackendException('"read_only" must be a boolean')
                    if not os.path.isabs(in_container_path):
                        raise DockerBackendException(
                            'Container mount point "{}" should be absolute'.format(
                                in_container_path))
                    if in_container_path.startswith(self._workDirInsideContainer):
                        raise DockerBackendException(
                            'Container mount point "{}" cannot be based in "{}"'.format(
                                in_container_path,
                                self._workDirInsideContainer))
                    self._extra_volume_mounts[host_path] = {
                        'bind': in_container_path,
                        'ro': read_only,
                    }
                continue

            # Not recognised option
            raise DockerBackendException(
                '"{}" key is not a recognised option'.format(key))

        # HACK: Try to prevent program path name being used in calls to addFileToBackend()
        if self.programPath().startswith('/tmp') and os.path.dirname(self.programPath()) == '/tmp':
            self._usedFileMapNames.add(os.path.basename(self.programPath()))

        # Initialise the docker client
        try:
            self._dc = _globalDockerClientPool.get_client()
            self._dc.ping()
        except Exception as e:
            _logger.error('Failed to connect to the Docker daemon')
            _logger.error(e)
            raise DockerBackendException(
                'Failed to connect to the Docker daemon')

        try:
            images = self._dc.images()
            assert isinstance(images, list)
            images = list(
                filter(lambda i: (i['RepoTags'] is not None) and self._dockerImageName in i['RepoTags'], images))
            if len(images) == 0:
                msg = 'Could not find docker image with name "{}"'.format(
                    self._dockerImageName)
                raise DockerBackendException(msg)
            else:
                if len(images) > 1:
                    msg = 'Found multiple docker images:\n{}'.format(
                        pprint.pformat(images))
                    _logger.error(msg)
                    raise DockerBackendException(msg)
                self._dockerImage = images[0]
                _logger.debug('Found Docker image:\n{}'.format(
                    pprint.pformat(self._dockerImage)))
        finally:
            # Release the client. We'll grab a new one in `run()`.
            _globalDockerClientPool.release_client()
            self._dc = None

    @property
    def name(self):
        return "Docker"

    @property
    def dockerStatsOnExitShimPathInContainer(self):
        if self._dockerStatsOnExitShimBinary == None:
            return None
        return self.getFilePathInBackend(self._dockerStatsOnExitShimBinary)

    @property
    def dockerStatsLogFileName(self):
        return 'exit_stats.json'

    @property
    def dockerStatsLogFileHost(self):
        return os.path.join(self.workingDirectory, self.dockerStatsLogFileName)

    @property
    def dockerStatsLogFileInContainer(self):
        return os.path.join(self.workingDirectoryInternal, self.dockerStatsLogFileName)

    def run(self, cmdLine, logFilePath, envVars):
        # run() may be called from a different thread than __init__() so grab a new client
        self._dc = _globalDockerClientPool.get_client()

        self._logFilePath = logFilePath
        self._outOfMemory = False
        outOfTime = False
        ulimits = []
        if self.stackLimit != None:
            # FIXME: Setting stack size in Docker seems broken right now.
            # See: https://github.com/docker/docker/issues/13521
            _logger.warning(
                "Setting stack size is probably broken. If you get crashes don't set it!")
            stackLimitInBytes = 0
            if self.stackLimit == 0:
                # Work out the maximum memory size, docker doesn't support
                # "unlimited" right now
                _logger.warning(
                    "Trying to emulate unlimited stack. Docker doesn't support setting it")
                if self.memoryLimit > 0:
                    # If a memory limit is set just set the stack size to the maximum we allow
                    # self.memoryLimit is in MiB, convert to bytes
                    stackLimitInBytes = self.memoryLimit * (2**20)
                else:
                    # No memory limit is set. Just use the amount of memory on system as an
                    # upper bound
                    stackLimitInBytes = psutil.virtual_memory().total + psutil.swap_memory().total
            elif self.stackLimit > 0:
                stackLimitInBytes = self.stackLimit * 1024
            # I'm assuming the stack limit is set in bytes here. I don't actually know if
            # this is the case.
            ulimits.append(docker.utils.Ulimit(name='stack',
                                               soft=stackLimitInBytes,
                                               hard=stackLimitInBytes))
            _logger.info(
                'Setting stack size limit to {} bytes'.format(stackLimitInBytes))

        extraHostCfgArgs = {}
        if len(ulimits) > 0:
            extraHostCfgArgs['ulimits'] = ulimits

        # Declare the volumes
        programPathInsideContainer = self.programPath()
        bindings = dict()

        if self._dockerStatsOnExitShimBinary:
            self.addFileToBackend(self._dockerStatsOnExitShimBinary, read_only=True)

        # Add aditional volumes
        for hostPath, (containerPath, read_only) in self._additionalHostContainerFileMaps.items():
            bindings[hostPath] = {'bind': containerPath, 'ro': read_only}

        # Try adding extra volumes
        for hostPath, props in self._extra_volume_mounts.items():
            bindings[hostPath] = props

        # Mandatory bindings
        bindings[self.workingDirectory] = {
            'bind': self.workingDirectoryInternal, 'ro': False}
        bindings[self.hostProgramPath] = {
            'bind': programPathInsideContainer, 'ro': True}

        _logger.debug('Declaring bindings:\n{}'.format(
            pprint.pformat(bindings)))

        extraContainerArgs = {}

        if self.memoryLimit > 0:
            # http://docs.docker.com/reference/run/#memory-constraints
            #
            # memory=L<inf, memory-swap=S<inf, L<=S
            # (specify both memory and memory-swap) The container is not allowed to use more than L bytes of memory, swap *plus* memory usage is limited by S.
            extraHostCfgArgs['mem_limit'] = '{}m'.format(self.memoryLimit)
            extraHostCfgArgs['memswap_limit'] = '{}m'.format(self.memoryLimit)
            _logger.info(
                'Setting memory limit to {} MiB'.format(self.memoryLimit))

        if self._userToUseInsideContainer != None:
            extraContainerArgs['user'] = self._userToUseInsideContainer
            _logger.info('Using user "{}" inside container'.format(
                self._userToUseInsideContainer))

        hostCfg = self._dc.create_host_config(
            binds=bindings,
            privileged=False,
            network_mode=None,
            **extraHostCfgArgs
        )

        # Modify the command line if necessary
        finalCmdLine = cmdLine
        if self._dockerStatsOnExitShimBinary:
            finalCmdLine = [self.dockerStatsOnExitShimPathInContainer,
                            self.dockerStatsLogFileInContainer] + finalCmdLine
        _logger.debug('Command line inside container:\n{}'.format(
            pprint.pformat(finalCmdLine)))

        # Finally create the container
        self._container = self._dc.create_container(
            image=self._dockerImage['Id'],
            command=finalCmdLine,
            environment=envVars,
            working_dir=self.workingDirectoryInternal,
            volumes=list(bindings.keys()),
            host_config=hostCfg,
            # The default. When all containers are created this way they will all
            # get the same proportion of CPU cycles.
            cpu_shares=0,
            **extraContainerArgs
        )
        _logger.debug('Created container:\n{}'.format(
            pprint.pformat(self._container['Id'])))
        if self._container['Warnings'] != None:
            _logger.warning('Warnings emitted when creating container:{}'.format(
                self._container['Warnings']))

        exitCode = None
        startTime = time.perf_counter()
        self._endTime = 0
        try:
            self._dc.start(container=self._container['Id'])
            timeoutArg = {}
            if self.timeLimit > 0:
                timeoutArg['timeout'] = self.timeLimit
                _logger.info('Using timeout {} seconds'.format(self.timeLimit))
            exitCode = self._dc.wait(
                container=self._container['Id'], **timeoutArg)
            if exitCode == -1:
                # FIXME: Does this even happen? Docker-py's documentation is
                # unclear.
                outOfTime = True
                _logger.info('Timeout occurred')
                exitCode = None
        except requests.exceptions.ReadTimeout as e:
            _logger.info('Timeout occurred')
            outOfTime = True
        except docker.errors.NotFound as e:
            _logger.error(
                'Failed to start/wait on container "{}".\nReason: {}'.format(self._container['Id'], str(e)))
        finally:
            self.kill()

        runTime = self._endTime - startTime
        userCPUTime = None
        sysCPUTime = None

        if self._dockerStatsOnExitShimBinary:
            # Try to extract the needed stats
            try:
                with open(self.dockerStatsLogFileHost, 'r') as f:
                    stats = json.load(f)
                    userCPUTime = float(stats['cgroups']['cpu_stats']['cpu_usage'][
                                        'usage_in_usermode']) / (10**9)
                    sysCPUTime = float(stats['cgroups']['cpu_stats']['cpu_usage'][
                                       'usage_in_kernelmode']) / (10**9)
            except Exception as e:
                _logger.error('Failed to retrieve stats from "{}"'.format(
                    self.dockerStatsLogFileHost))
                _logger.error(str(e))
                _logger.error(traceback.format_exc())

        return BackendResult(exitCode=exitCode,
                             runTime=runTime,
                             oot=outOfTime,
                             oom=self._outOfMemory,
                             userCpuTime=userCPUTime,
                             sysCpuTime=sysCPUTime)

    def kill(self):
        try:
            self._killLock.acquire()
            self._endTime = time.perf_counter()
            if self._container != None:
                _logger.info('Stopping container:{}'.format(
                    self._container['Id']))
                try:
                    containerStatus = self._dc.inspect_container(
                        self._container['Id'])
                    if containerStatus["State"]["Running"]:
                        self._dc.kill(self._container['Id'])
                except docker.errors.APIError as e:
                    _logger.error('Failed to kill container:"{}".\n{}'.format(
                        self._container['Id'], str(e)))

                # Write logs to file (note we get binary in Python 3, not sure
                # about Python 2)
                with open(self._logFilePath, 'wb') as f:
                    logData = self._dc.logs(container=self._container['Id'],
                                            stdout=True, stderr=True, timestamps=False,
                                            tail='all', stream=False)
                    _logger.info('Writing log to {}'.format(self._logFilePath))
                    f.write(logData)

                # Record if OOM occurred
                containerInfo = self._dc.inspect_container(
                    container=self._container['Id'])
                self._outOfMemory = containerInfo['State']['OOMKilled']
                assert isinstance(self._outOfMemory, bool)

                try:
                    _logger.info('Destroying container:{}'.format(
                        self._container['Id']))
                    self._dc.remove_container(
                        container=self._container['Id'], force=True)
                except docker.errors.APIError as e:
                    _logger.error('Failed to remove container:"{}".\n{}'.format(
                        self._container['Id'], str(e)))
                self._container = None
        finally:
            # FIXME: Should we remove this release? We can probably get a slightly
            # better performance by doing this.
            _globalDockerClientPool.release_client()
            self._killLock.release()

    def programPath(self):
        return '/tmp/{}'.format(os.path.basename(self.hostProgramPath))

    def checkToolExists(self, toolPath):
        if self._skipToolExistsCheck:
            _logger.info('Skipping tool check')
            return
        assert os.path.isabs(toolPath)
        # HACK: Is there a better way to do this?
        _logger.debug('Checking tool "{}" exists in image'.format(toolPath))
        tempContainer = self._dc.create_container(image=self._dockerImage['Id'],
                                                  command=['ls', toolPath])
        _logger.debug('Created temporary container: {}'.format(
            tempContainer['Id']))
        self._dc.start(container=tempContainer['Id'])
        exitCode = self._dc.wait(container=tempContainer['Id'])
        self._dc.remove_container(container=tempContainer['Id'], force=True)
        if exitCode != 0:
            raise DockerBackendException(
                'Tool "{}" does not exist in Docker image'.format(toolPath))

    @property
    def workingDirectoryInternal(self):
        # Return the path to the working directory that will be used inside the
        # container
        return self._workDirInsideContainer

    def addFileToBackend(self, path, read_only):
        if not os.path.isabs(path):
            raise DockerBackendException('path must be absolute')
        fileName = os.path.basename(path)

        if not os.path.exists(path):
            raise DockerBackendException(
                'File "{}" does not exist'.format(path))

        if not isinstance(read_only, bool):
            raise DockerBackendException('"read_only" must be boolean')

        # FIXME: This mapping is lame. We could do something more sophisticated
        # to avoid this limitation.
        if fileName in self._usedFileMapNames:
            raise DockerBackendException(
                'Mapping identicaly named file is not supported')
        self._additionalHostContainerFileMaps[
            path] = ( os.path.join('/tmp', fileName), read_only)
        _logger.debug('Adding mapping "{}" => "{}"'.format(
            path,
            self._additionalHostContainerFileMaps[path])
        )
        for _, props in self._extra_volume_mounts.items():
            if self._additionalHostContainerFileMaps[path] == props['bind']:
                raise DockerBackendException(
                    'Cannot add path "{}". It is already in use by "{}"'.format(
                        path, self._extra_volume_mounts))
        self._usedFileMapNames.add(fileName)

    def getFilePathInBackend(self, hostPath):
        try:
            file_path, _ = self._additionalHostContainerFileMaps[hostPath]
            return file_path
        except KeyError as e:
            raise DockerBackendException(
                '"{}" was not given to addFileToBackend()'.format(hostPath))


def get():
    return DockerBackend
