"""
Parse one of KLEEs "info" files
"""

import re
import logging
from datetime import datetime, timedelta
from ..exceptions import InputError

_logger = logging.getLogger(__name__)

class Info:
    """
    Contains the information from a KLEE "info" file
    """
    __re_pid = re.compile(r"PID: (\d+)\r?\n")
    __re_start = re.compile(r"Started: (\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\r?\n")
    __re_finish = re.compile(r"Finished: (\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\r?\n")
    __re_elapsed = re.compile(r"Elapsed: (\d{2}):(\d{2}):(\d{2})\r?\n")
    __re_explored = re.compile(r"KLEE: done: explored paths = (\d+)\r?\n")
    __re_constructs_per_query = re.compile(r"KLEE: done: avg\. constructs per query = (\d+)\r?\n")
    __re_queries = re.compile(r"KLEE: done: total queries = (\d+)\r?\n")
    __re_queries_sat = re.compile(r"KLEE: done: valid queries = (\d+)\r?\n")
    __re_queries_nsat = re.compile(r"KLEE: done: invalid queries = (\d+)\r?\n")
    __re_queries_cex = re.compile(r"KLEE: done: query cex = (\d+)\r?\n")
    __re_instructions = re.compile(r"KLEE: done: total instructions = (\d+)\r?\n")
    __re_paths = re.compile(r"KLEE: done: completed paths = (\d+)\r?\n")
    __re_tests = re.compile(r"KLEE: done: generated tests = (\d+)\r?\n")

    @staticmethod
    def __force_match(regex, line, message, path):
        match = regex.fullmatch(line)
        if not match:
            raise InputError(message.format(path))
        return match

    def __parse_searcher(self, infofile, path):
        """Parse the searcher description."""
        line = infofile.readline().rstrip()
        if line != r"BEGIN searcher description":
            raise InputError('Info file "{}" does not contain a valid begin searcher tag in line 4'.format(path))
        self.searcher = []
        while True:
            line = infofile.readline()
            if line == "":
                raise InputError('Info file "{}" missing end searcher tag'.format(path))
            line = line.rstrip()
            if line == r"END searcher description":
                break
            self.searcher.append(line)

    def __init__(self, path: "Path to a KLEE info file."):
        """Open a KLEE "info" file."""
        _logger.debug('Creating Info from "{}"'.format(path))
        try:
            with open(path) as infofile:
                line = infofile.readline()
                if len(line) == 0:
                    self.empty = True
                    return
                self.empty = False
                self.command = line.rstrip()
                if len(self.command) == 0:
                    raise InputError('Info file "{}" has empty command'.format(path))

                match = self.__force_match(self.__re_pid, infofile.readline(), 'Info file "{}" does not contain a valid PID entry in line 2', path)
                self.pid = int(match.group(1))

                match = self.__force_match(self.__re_start, infofile.readline(), 'Info file "{}" does not contain a valid started entry in line 3', path)
                self.start = datetime(*[int(match.group(x)) for x in range(1, 7)])

                self.__parse_searcher(infofile, path)

                match = self.__force_match(self.__re_finish, infofile.readline(), 'Info file "{}" does not contain a valid finished entry 1 line after the end searcher tag', path)
                self.finish = datetime(*[int(match.group(x)) for x in range(1, 7)])

                match = self.__force_match(self.__re_elapsed, infofile.readline(), 'Info file "{}" does not contain a valid elapsed entry 2 lines after the end searcher tag', path)
                self.elapsed = timedelta(0, int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)))

                match = self.__force_match(self.__re_explored, infofile.readline(), 'Info file "{}" does not contain a valid explored paths entry 3 lines after the end searcher tag', path)
                self.explored_paths = int(match.group(1))

                nextLine = infofile.readline()
                try:
                  match = self.__force_match(self.__re_constructs_per_query,
                      nextLine,
                      'Info file "{}" does not contain a valid avg. constructs per query entry 4 lines after the end searcher tag',
                      path)
                  self.constructs_per_query = int(match.group(1))
                  nextLine = infofile.readline() # Get the next line for the next __force_match
                except InputError:
                  # This line might not be emitted if there are no queries
                  self.constructs_per_query = 0

                match = self.__force_match(self.__re_queries, nextLine, 'Info file "{}" does not contain a valid total queries entry 5 lines after the end searcher tag', path)
                self.queries = int(match.group(1))

                match = self.__force_match(self.__re_queries_sat, infofile.readline(), 'Info file "{}" does not contain a valid valid queries entry 6 lines after the end searcher tag', path)
                self.satisfiable_queries = int(match.group(1))

                match = self.__force_match(self.__re_queries_nsat, infofile.readline(), 'Info file "{}" does not contain a valid invalid queries entry 7 lines after the end searcher tag', path)
                self.unsatisfiable_queries = int(match.group(1))

                match = self.__force_match(self.__re_queries_cex, infofile.readline(), 'Info file "{}" does not contain a valid query cex entry 8 lines after the end searcher tag', path)
                self.cex = int(match.group(1))

                line = infofile.readline().rstrip()
                if len(line) > 0:
                    raise InputError('Info file "{}" does not contain an empty line 9 lines after the end searcher tag'.format(path))

                match = self.__force_match(self.__re_instructions, infofile.readline(), 'Info file "{}" does not contain a valid total instruction entry 10 lines after the end searcher tag', path)
                self.instructions = int(match.group(1))

                match = self.__force_match(self.__re_paths, infofile.readline(), 'Info file "{}" does not contain a valid a completed paths entry 11 lines after the end searcher tag', path)
                self.completed_paths = int(match.group(1))

                match = self.__force_match(self.__re_tests, infofile.readline(), 'Info file "{}" does not contain a valid a generated tests entry 12 lines after the end searcher tag', path)
                self.tests = int(match.group(1))

                line = infofile.readline()
                if len(line) > 0:
                    raise InputError('Info file "{}" did not end as expected.'.format(path))
        except FileNotFoundError:
            raise InputError('Info file "{}" does not exist.'.format(path))
