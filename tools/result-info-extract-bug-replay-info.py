#!/usr/bin/env python
# Copyright (c) 2016, Daniel Liew
# This file is covered by the license in LICENSE-SVCB.txt
# vim: set sw=4 ts=4 softtabstop=4 expandtab:
"""
Read a result info describing a set of KLEE test case
that were done natively and compare this to what KLEE expected
to confirm or refute a reported bug
"""
from load_klee_runner import add_KleeRunner_to_module_search_path
from load_klee_analysis import add_kleeanalysis_to_module_search_path
from load_native_analysis import add_nativeanalysis_to_module_search_path
add_KleeRunner_to_module_search_path()
add_kleeanalysis_to_module_search_path()
add_nativeanalysis_to_module_search_path()
from KleeRunner import ResultInfo
import KleeRunner.DriverUtil as DriverUtil
import KleeRunner.InvocationInfo
import KleeRunner.util
import kleeanalysis.analyse
import kleeanalysis.kleedir.test
import nativeanalysis.analyse

import argparse
import logging
import os
import pprint
import re
import sys
import yaml

_logger = logging.getLogger(__name__)


def main(args):
    global _logger
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result_info_file',
                        help='Result info file',
                        type=argparse.FileType('r'))
    parser.add_argument('-m',
        '--mismatch-is-error',
        dest='mismatch_is_error',
        default=False,
        action='store_true',
    )
    parser.add_argument('-o', '--output-yaml',
                        dest='output_yaml',
                        type=argparse.FileType('w'),
                        default=sys.stdout,
                        help='Output location (default stdout)')

    DriverUtil.parserAddLoggerArg(parser)
    pargs = parser.parse_args()
    DriverUtil.handleLoggerArgs(pargs, parser)

    _logger.info('Loading "{}"...'.format(pargs.result_info_file.name))
    resultInfos, resultInfoMisc  = ResultInfo.loadResultInfos(pargs.result_info_file)
    _logger.info('Loading complete')

    # Check the misc data
    if resultInfoMisc is None:
        _logger.error('Expected result info to have misc data')
        return 1
    if resultInfoMisc['runner'] != 'NativeReplay':
        _logger.error('Expected runner to have been NativeReplay but was "{}"'.format(
            resultInfoMisc['runner']))
        return 1

    program_to_test_case_replay_info = dict()
    program_key_to_full_path_map = dict() # Sanity check

    def get_or_insert_test_case_replay_info(program_name):
        if program_name in program_to_test_case_replay_info:
            return program_to_test_case_replay_info[program_name]

        # Blank template
        test_case_replay_info = {
            'test_cases': {},
            'augmented_spec_file': ''
        }
        program_to_test_case_replay_info[program_name] = test_case_replay_info
        return test_case_replay_info

    for result_index, r in enumerate(resultInfos):
        _logger.info('Processing {}/{}'.format(result_index + 1, len(resultInfos)))
        ii = r.RawInvocationInfo
        program_path = ii['program']
        program_name = os.path.basename(program_path)

        augmented_spec_path = ii['misc']['augmented_spec_file']
        # Sanity check: Check that the program name can be used as a unique identifier
        # by checking that the same augmented spec path is used. We can't use the full
        # program path because we sometime mix builds (e.g. ubsan/asan/normal) of the
        # effectively the same program
        if program_name in program_key_to_full_path_map:
            if augmented_spec_path != program_key_to_full_path_map[program_name]:
                _logger.error('program file name {} is not unique'.format(program_name))
                return 1
        else:
            program_key_to_full_path_map[program_name] = augmented_spec_path

        test_case_replay_info = get_or_insert_test_case_replay_info(program_name)
        test_case_replay_info['augmented_spec_file'] = augmented_spec_path # FIXME: fp-bench specific
        ktest_file = ii['ktest_file']

        bug_replay_build_type = ii['misc']['bug_replay_build_type']
        assert bug_replay_build_type == 'normal' or bug_replay_build_type == 'asan' or bug_replay_build_type == 'ubsan'

        # Start with empty template which will be filled
        test_case_info = {
            'confirmed': None,
            'fp_bench_task': ii['misc']['fp_bench_task'], # FIXME: fp-bench specific
            'description': "",
            'build_replay_build_type': bug_replay_build_type,
        }
        test_case_replay_info['test_cases'][ktest_file] = test_case_info

        # Load the the ktest file
        _logger.debug('Trying to load "{}"'.format(ktest_file))
        test_case_obj = kleeanalysis.kleedir.test.Test(ktest_file)

        # Get the outcome of natively executing the test case
        test_outcome = nativeanalysis.analyse.get_test_case_run_outcome(r.GetInternalRepr())
        _logger.debug('Got test case outcome: {}'.format(test_outcome))

        # Helper function
        def match_test_case(expected_type, skip_lib_calls):
            if isinstance(test_outcome, expected_type):
                source_location_matches, err = check_error_location_match(
                    test_case_obj,
                    test_outcome,
                    skip_lib_calls=skip_lib_calls
                )
                if source_location_matches:
                    test_case_info['confirmed'] = True
                else:
                    test_case_info['confirmed'] = False
                    test_case_info['description'] = err
            else:
                test_case_info['confirmed'] = False
                test_case_info['description'] = "Expected abort but on replay was {}".format(
                    get_type_name(test_outcome))

        # Branch on the different KLEE test case types
        if test_case_obj.abort:
            match_test_case(nativeanalysis.analyse.AbortError, skip_lib_calls=True)
        elif test_case_obj.assertion:
            match_test_case(nativeanalysis.analyse.AssertError, skip_lib_calls=True)
        elif test_case_obj.division:
            # Integer division by zero.
            if isinstance(test_outcome, nativeanalysis.analyse.UBSanError):
                assert bug_replay_build_type == 'ubsan'
                match_test_case(nativeanalysis.analyse.UBSanError, skip_lib_calls=False)
            elif isinstance(test_outcome, nativeanalysis.analyse.ArithmeticError):
                assert bug_replay_build_type == 'normal'
                match_test_case(nativeanalysis.analyse.ArithmeticError, skip_lib_calls=False)
            else:
                raise Exception('Unhandled case')
        elif test_case_obj.free:
            assert bug_replay_build_type == 'asan'
            if is_asan_free_error(test_outcome):
                source_location_matches, err = check_error_location_match(
                    test_case_obj,
                    test_outcome,
                    skip_lib_calls=True
                )
                if source_location_matches:
                    test_case_info['confirmed'] = True
                else:
                    test_case_info['confirmed'] = False
                    test_case_info['description'] = err
            else:
                test_case_info['confirmed'] = False
                test_case_info['description'] = ("Expected use after free "
                    "but on replay was {}".format(test_outcome))
        elif test_case_obj.ptr:
            assert bug_replay_build_type == 'asan'
            if is_asan_ptr_error(test_outcome):
                source_location_matches, err = check_error_location_match(
                    test_case_obj,
                    test_outcome,
                    skip_lib_calls=True
                )
                if source_location_matches:
                    test_case_info['confirmed'] = True
                else:
                    test_case_info['confirmed'] = False
                    test_case_info['description'] = err
            else:
                test_case_info['confirmed'] = False
                test_case_info['description'] = ("Expected invalid pointer "
                    "but on replay was {}".format(test_outcome))
        elif test_case_obj.overshift:
            # FIXME: We can't confirm these right now because we only check for 
            # integer division by zero in UBSan builds.
            test_case_info['confirmed'] = None
            test_case_info['description'] = ("FIXME: confirming overshift not supported")
        else:
            _logger.error('Unhandled test case type:\n{}'.format(test_case_obj))
            return 1

        if test_case_info['confirmed'] is not True:
            _logger.warning("{}:\n{}".format(
                program_name,
                pprint.pformat(test_case_info))
            )
            if pargs.mismatch_is_error:
                _logger.error('Mismatch treated as error')
                return 1

    # Now emit as YAML
    as_yaml = yaml.dump(program_to_test_case_replay_info, default_flow_style=False)
    pargs.output_yaml.write(as_yaml)
    return 0

def is_asan_ptr_error(test_outcome):
    if not isinstance(test_outcome, nativeanalysis.analyse.ASanError):
        return False
    
    ptr_error_types = [
        'stack-buffer-overflow',
        'heap-buffer-overflow',
        'global-buffer-overflow',
    ]
    for error_type in ptr_error_types:
        if test_outcome.type == error_type:
            return True
    return False

def is_asan_free_error(test_outcome):
    if not isinstance(test_outcome, nativeanalysis.analyse.ASanError):
        return False
    
    ptr_error_types = [
        'stack-use-after-return',
        'stack-use-after-scope',
        'heap-use-after-free',
    ]
    for error_type in ptr_error_types:
        if test_outcome.type == error_type:
            return True
    return False

def get_type_name(t):
    return type(t).__name__

def check_error_location_match(test_case_obj, test_outcome, skip_lib_calls):
    """
        returns (t, msg)
        If `t` is True then the locations match and `msg` in None.
        If `t` is False then the locations don't match and `msg` is an error
        message that describes this mismatch.
    """
    assert isinstance(test_case_obj, kleeanalysis.kleedir.test.Test)
    # FIXME: Check `test_outcome` type
    assert isinstance(skip_lib_calls, bool)
    # TODO: Check entire stacktrace matches?
    assert len(test_outcome.stack_trace) > 0
    for sf in test_outcome.stack_trace:
        if sf.lib and skip_lib_calls:
            continue
        if sf.lib:
            return (False, "Stacktrace contains libcall")

        test_case_error_file = test_case_obj.error.file
        test_outcome_error_file = sf.source_file
        if test_case_error_file != test_outcome_error_file:
            return (False, "Error file names don't match (\"{}\" and \"{}\")".format(
                test_case_error_file,
                test_outcome_error_file)
            )

        # Match up line numbers
        if test_case_obj.error.line != sf.line_number:
            return (False, "Error file names match but line numbers don't "
                " ({} and {})".format(test_case_obj.error.line, sf.line_number)
            )

        # Location matches
        return (True, None)

    if skip_lib_calls:
        return (False, "All calls in stacktrace were libcalls which were skipped")
    raise Exception('Matching error locations failed unexpectedly')


if __name__ == '__main__':
    sys.exit(main(sys.argv))

