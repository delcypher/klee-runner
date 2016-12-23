"""Represent KLEE working directories"""
# vim: set sw=4 ts=4 softtabstop=4 expandtab:

import glob
import logging
import os
import re

from .info import Info
from .test import Test
from ..exceptions import InputError

_logger = logging.getLogger(__name__)

class KleeDir:
    """A KLEE working directory"""

    def __init__(self, path: "Path to a KLEE working directory."):
        """
        Open a KLEE working directory.
        """
        _logger.debug('Creating KleeDir from "{}"'.format(path))
        self.path = path
        try:
            self.info = Info(os.path.join(path, "info"))
        except InputError as ie:
            _logger.debug(ie)
            self.info = None

        self._lost_test_cases = 0

        # Note: Tests should be returned in order so that all properties that use
        # it (e.g. `abort_errors`) are also ordered.
        test_files = sorted(glob.glob(os.path.join(glob.escape(path), 'test*.ktest')))
        if self.is_valid:
            # Check the number of test matches what we expect
            if len(test_files) != self.info.tests:
                msg = "Expected {} tests but found {} for KLEE directory \"{}\"".format(
                    self.info.tests,
                    len(test_files),
                    self.path)
                _logger.warning(msg)
                if len(test_files) < self.info.tests:
                    # FIXME: This is a hack around a bug in KLEE
                    # https://github.com/klee/klee/issues/555
                    self._lost_test_cases += 1
                else:
                    # Should never happen
                    raise Exception(msg)
            elif self.info.tests < self.info.completed_paths:
                # FIXME: This is legitimate if `-emit-all-errors` is false
                # (KLEE's default). I want to be warned about this for now
                # though.
                _logger.warning(
                    ("Generated tests ({}) < number of completed paths({}) in "
                     "KLEE directory \"{}\"").format(
                    self.info.tests,
                    self.info.completed_paths,
                    self.path))
                self._lost_test_cases += 1

        self.tests = []
        for test_file_path in test_files:
            self.tests.append(Test(test_file_path))


        try:
            with open(os.path.join(path, "messages.txt")) as file:
                self.messages = file.readlines()
        except FileNotFoundError:
            self.messages = "Could not open {}".format(os.path.join(path, "messages.txt"))
        try:
            with open(os.path.join(path, "warnings.txt")) as file:
                self.warnings = file.readlines()
        except FileNotFoundError:
            self.warnings = "Could not open {}".format(os.path.join(path, "warnings.txt"))

    @property
    def lost_test_cases(self):
        return self._lost_test_cases

    @property
    def halt_timer_invoked(self):
        """ Return True iff halt timer was invoked """
        halt_timer_regex = re.compile(r'^KLEE: HaltTimer invoked')
        for line in self.messages:
            if halt_timer_regex.search(line) is not None:
                return True
        return False

    @property
    def is_valid(self):
        """If the KLEE directory is in a valid state"""
        return self.info is not None and not self.info.empty

    @property
    def abort_errors(self):
        """Returns all abortions"""
        return (test for test in self.tests if test.abort is not None)

    @property
    def assertion_errors(self):
        """Returns all assertion failures"""
        return (test for test in self.tests if test.assertion is not None)

    @property
    def division_errors(self):
        """Returns all division failures"""
        return (test for test in self.tests if test.division is not None)

    @property
    def execution_errors(self):
        """Returns all execution failures"""
        return (test for test in self.tests if test.execution_error is not None)

    @property
    def free_errors(self):
        """Returns all use after free errors"""
        return (test for test in self.tests if test.free is not None)

    @property
    def overflow_errors(self):
        """Returns all overflow failures"""
        return (test for test in self.tests if test.overflow is not None)

    @property
    def overshift_errors(self):
        """Returns all overshift failures"""
        return (test for test in self.tests if test.overshift is not None)

    @property
    def ptr_errors(self):
        """Returns all derefence invalid ptr failures"""
        return (test for test in self.tests if test.ptr is not None)

    @property
    def read_only_errors(self):
        """Returns all user error failures"""
        return (test for test in self.tests if test.readonly_error is not None)

    @property
    def user_errors(self):
        """Returns all user error failures"""
        return (test for test in self.tests if test.user_error is not None)

    @property
    def early_terminations(self):
        """Returns all early terminations"""
        return (test for test in self.tests if test.early is not None)

    @property
    def successful_terminations(self):
        """Returns all terminations that terminated without error and
           are a complete execution (i.e. did not terminate early)
        """
        return (test for test in self.tests if test.is_successful_termination)

    @property
    def misc_errors(self):
        """Returns all uncategorized failures"""
        return (test for test in self.tests if test.misc_error is not None)

    @property
    def errors(self):
        """Returns all tests for errors. This does not include early termination"""
        return (test for test in self.tests if test.error is not None)
