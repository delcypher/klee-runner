# vim: set sw=4 ts=4 softtabstop=4 expandtab:
import logging
import os
from . RunnerBase import RunnerBaseClass

_logger = logging.getLogger(__name__)


class KleeRunnerException(Exception):

    def __init__(self, msg):
        # pylint: disable=super-init-not-called
        self.msg = msg


class KleeRunner(RunnerBaseClass):

    def __init__(self, invocationInfo, workingDirectory, rc, ctx):
        # pylint: disable=too-many-branches
        _logger.debug('Initialising {}'.format(invocationInfo.Program))

        # FIXME: We should have a schema for this so we don't have to write
        # this
        if 'max_time' in rc:
            raise KleeRunnerException(
                "'max_time' should not be specified use 'explore_max_time' and"
                " 'generate_tests_max_time' instead")

        self.exploreMaxTime = 0
        self.generateTestsMaxTime = 0
        if not 'explore_max_time' in rc:
            raise KleeRunnerException("'explore_max_time' must be specified")
        self.exploreMaxTime = rc['explore_max_time']
        if not isinstance(self.exploreMaxTime, int):
            raise KleeRunnerException("'explore_max_time' must be an integer")
        if self.exploreMaxTime < 0:
            raise KleeRunnerException("'explore_max_time' must be >= 0")

        if not 'generate_tests_max_time' in rc:
            raise KleeRunnerException(
                "'generate_tests_max_time' must be specified")
        self.generateTestsMaxTime = rc['generate_tests_max_time']
        if not isinstance(self.generateTestsMaxTime, int):
            raise KleeRunnerException(
                "'generate_tests_max_time' must be an integer")
        if self.generateTestsMaxTime < 0:
            raise KleeRunnerException("'generate_tests_max_time' must be >= 0")

        # Derive max time from the specified times
        maxTime = self.exploreMaxTime + self.generateTestsMaxTime
        assert maxTime >= 0
        rc['max_time'] = maxTime
        _logger.info(
            'Forcing max_time to be {} seconds'.format(rc['max_time']))

        self.kleeMaxMemory = 0
        if 'klee_max_memory' in rc:
            self.kleeMaxMemory = rc['klee_max_memory']
        if not isinstance(self.kleeMaxMemory, int):
            raise KleeRunnerException("'klee_max_memory' must be an integer")
        if self.kleeMaxMemory < 0:
            raise KleeRunnerException("'klee_max_memory' must be >= 0")

        if invocationInfo.CoverageDir is not None:
            raise KleeRunnerException('coverage_dir is not supported by this runner')

        if invocationInfo.AttachGDB:
            raise KleeRunner('attach_gdb is not supported by this runner')

        self.outputDir = None

        super(KleeRunner, self).__init__(invocationInfo, workingDirectory, rc, ctx)

        if self.maxMemoryInMiB < self.kleeMaxMemory:
            raise KleeRunnerException(
                "'klee_max_memory' must be >= max_memory")

        # Sanity checks

        # We handle several options ourselves. Don't let the user set these
        disallowedArgs = ['-maxtime',
                          '-max-memory',
                          '-replay-ktest-file']
        for disallowedArg in disallowedArgs:
            for arg in list(self.additionalArgs) + self.InvocationInfo.ExtraKleeCommandLineArguments:
                convertedArg = arg
                if convertedArg.startswith('--'):
                    # change --foo into -foo
                    convertedArg = convertedArg[1:]
                if convertedArg.startswith(disallowedArg):
                    raise KleeRunnerException(
                        '{} must not be specified'.format(disallowedArg))

    @property
    def name(self):
        return "klee"

    def getResults(self):
        r = super(KleeRunner, self).getResults()
        r['klee_dir'] = self.outputDir
        return r

    def run(self):
        # Build the command line
        cmdLine = [self.toolPath] + self.additionalArgs

        # KLEE outputdir
        outputDirInBackend = os.path.join(
            self.workingDirectoryInBackend, "klee-wd")
        cmdLine.append('-output-dir={}'.format(outputDirInBackend))
        self.outputDir = os.path.join(self.workingDirectory, "klee-wd")

        # We use a combination of KLEE's memory limit enforcement and external
        # enforcement. The hope is that KLEE's own enforcement will mean we
        # will actually get test cases logged. External enforcement will
        # likely kill KLEE aggressively causing the test cases to be lost.
        cmdLine.append('-max-memory={}'.format(self.kleeMaxMemory))

        # Set maximum exploration time
        cmdLine.append('-max-time={}'.format(self.exploreMaxTime))

        # Add extra KLEE arguments
        cmdLine.extend(self.InvocationInfo.ExtraKleeCommandLineArguments)

        # If there's a KTest file use it.
        if self.InvocationInfo.KTestFile:
            if not os.path.exists(self.InvocationInfo.KTestFile):
                raise KleeRunnerException(
                    'Specified KTest file "{}" does not exist'.format(
                        self.InvocationInfo.KTestFile))
            cmdLine.append(
                '-replay-ktest-file={}'.format(self.InvocationInfo.KTestFile))
            # Make sure the backend knows that this file needs to be available
            # in the backend.
            self._backend.addFileToBackend(self.InvocationInfo.KTestFile, read_only=True)

        # Add the LLVM bitcode file
        cmdLine.append(self.programPathArgument)

        # Now add the command line arguments for program under test
        cmdLine.extend(self.InvocationInfo.CommandLineArguments)

        backendResult = self.runTool(
            cmdLine, envExtra=self.InvocationInfo.EnvironmentVariables)
        if backendResult.outOfTime:
            _logger.warning('Hard timeout hit')


def get():
    return KleeRunner
