# vim: set sw=2 ts=2 softtabstop=2 expandtab:
from . RunnerBase import RunnerBaseClass
import logging
import os
import re
import psutil
import yaml

_logger = logging.getLogger(__name__)

class NativeReplayRunnerException(Exception):
  def __init__(self, msg):
    self.msg = msg

class NativeReplayRunner(RunnerBaseClass):
  def __init__(self, invocationInfo, workingDirectory, rc):
    _logger.debug('Initialising {}'.format(invocationInfo.Program))

    # Tool path doesn't mean anything here
    if 'tool_path' in rc:
      raise NativeReplayRunnerException('"tool_path" should not be specified')

    # Check have KTest file
    if invocationInfo.KTestFile == None:
      raise NativeReplayRunnerException('KTest file must be specified')
    if not os.path.exists(invocationInfo.KTestFile):
      raise NativeReplayRunnerException('KTest file "{}" does not exist'.format(
        invocationInfo.KTestFile))

    super(NativeReplayRunner, self).__init__(invocationInfo, workingDirectory, rc)

  @property
  def name(self):
    return "Native replay"

  def getResults(self):
    r = super(NativeReplayRunner, self).getResults()
    return r

  def _checkToolExistsInBackend(self):
    # There is no "tool" here so don't check if it exists.
    pass

  def _setupToolPath(self, rc):
    self.toolPath = None
    pass

  def run(self):
    # Build the command line
    cmdLine = [ self.toolPath ] + self.additionalArgs
    cmdLine = [ self.programPathArgument ] + self.additionalArgs

    # Make sure the backend knows that this file needs to be available in the backend.
    self._backend.addFileToBackend(self.InvocationInfo.KTestFile)

    # Now add the command line arguments for program under test
    cmdLine.extend(self.InvocationInfo.CommandLineArguments)

    env = self.InvocationInfo.EnvironmentVariables
    env['KTEST_FILE'] = self._backend.getFilePathInBackend(self.InvocationInfo.KTestFile)

    backendResult = self.runTool(cmdLine, envExtra = env)
    if backendResult.outOfTime:
      _logger.warning('Hard timeout hit')

def get():
  return NativeReplayRunner

