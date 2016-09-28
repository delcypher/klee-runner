import argparse
from . import ConfigLoader
import logging
import os
import traceback

_logger = logging.getLogger(__name__)

def parserAddLoggerArg(parser):
  assert isinstance(parser, argparse.ArgumentParser)
  parser.add_argument("-l","--log-level",type=str, default="info", dest="log_level", choices=['debug','info','warning','error'])
  return

def handleLoggerArgs(pargs):
  assert isinstance(pargs, argparse.Namespace)
  logLevel = getattr(logging, pargs.log_level.upper(),None)
  if logLevel == logging.DEBUG:
    logFormat = '%(levelname)s:%(threadName)s: %(filename)s:%(lineno)d %(funcName)s()  : %(message)s'
  else:
    logFormat = '%(levelname)s:%(threadName)s: %(message)s'

  logging.basicConfig(level=logLevel, format=logFormat)

def loadRunnerConfig(configFilePath):
  # Load runner configuration
  config = None
  try:
    _logger.debug('Loading configuration from "{}"'.format(configFilePath))
    config = ConfigLoader.load(configFilePath)
  except ConfigLoader.ConfigLoaderException as e:
    _logger.error(e)
    _logger.debug(traceback.format_exc())
    return (None, False)
  return (config, True)

def setupWorkingDirectory(workingDir):
  # Setup the working directory
  absWorkDir = os.path.abspath(workingDir)
  if os.path.exists(absWorkDir):
    # Check it's a directory and it's empty
    if not os.path.isdir(absWorkDir):
      _logger.error('"{}" exists but is not a directory'.format(absWorkDir))
      return (None, False)

    absWorkDirRootContents = next(os.walk(absWorkDir, topdown=True))
    if len(absWorkDirRootContents[1]) > 0 or len(absWorkDirRootContents[2]) > 0:
      _logger.error('"{}" is not empty ({},{})'.format(absWorkDir,
        absWorkDirRootContents[1], absWorkDirRootContents[2]))
      return (None, False)
  else:
    # Try to create the working directory
    try:
      os.mkdir(absWorkDir)
    except Exception as e:
      _logger.error('Failed to create working_dirs_root "{}"'.format(absWorkDirsRoot))
      _logger.error(e)
      _logger.debug(traceback.format_exc())
      return (None, False)
  return (absWorkDir, True)

