#!/usr/bin/env python
# vim: set sw=4 ts=4 softtabstop=4 expandtab:
"""
Perform verification of a klee-runner result yaml file and associated working
directory.
"""

import argparse
import logging
from enum import Enum
import os
import pprint
# pylint: disable=wrong-import-position
from load_klee_analysis import add_kleeanalysis_to_module_search_path
from load_klee_runner import add_KleeRunner_to_module_search_path
add_kleeanalysis_to_module_search_path()
add_KleeRunner_to_module_search_path()
import KleeRunner.ResultInfo
import KleeRunner.DriverUtil as DriverUtil
import kleeanalysis.analyse
import kleeanalysis.verificationtasks
from kleeanalysis.analyse import KleeRunnerResult, \
    get_klee_verification_results_for_fp_bench, \
    get_klee_dir_verification_summary_across_tasks, \
    KleeResultCorrect, \
    KleeResultIncorrect, \
    KleeResultUnknown, \
    KleeResultMatchSpec, \
    KleeResultMismatchSpec, \
    KleeResultUnknownMatchSpec, \
    KleeMatchSpecReason, \
    KleeResultUnknownReason

_logger = logging.getLogger(__name__)

def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--result-info-file",
                        dest="result_info_file",
                        help="result info file. (Default stdin)",
                        type=argparse.FileType('r'),
                        default=sys.stdin)
    parser.add_argument("--dump-spec-mismatches",
        dest="dump_spec_mismatches",
        action="store_true",
        default=False,
    )
    parser.add_argument("--dump-spec-match-unknown-expect-incorrect",
        dest="dump_spec_match_unknown_expect_incorrect",
        action="store_true",
        default=False,
    )
    parser.add_argument("--disallow-invalid-klee-dirs",
        dest="allow_invalid_klee_dir",
        action="store_false",
        default=True
    )
    parser.add_argument("--dump-verified-incorrect-no-assert-fail",
        dest="dump_verified_incorrect_no_assert_fail",
        action="store_true",
        default=False
    )
    parser.add_argument("--ignore-error-runs",
        dest="ignore_error_runs",
        action="store_true",
        default=False,
        help="Carry on report even if failed runs occurred",
    )
    parser.add_argument("--categories",
       nargs='+',
       help='Only analyse results where the bencmark belongs to all specified categories',
       default=[]
    )
    DriverUtil.parserAddLoggerArg(parser)

    args = parser.parse_args(args=argv)
    DriverUtil.handleLoggerArgs(args, parser)

    exitCode = 0
    _logger.info('Reading result infos from {}'.format(args.result_info_file.name))

    # Result counters
    summaryCounters = dict()
    multipleOutcomes = []
    for enum in list(KleeRunnerResult):
        summaryCounters[enum] = 0

    # Verification results map the type to
    # tuple (<identifier>, <result>)
    verification_result_type_to_info = dict() # results on per task basis
    verification_result_type_to_benchmark = dict() #  results on per benchmark basis
    for t in [ KleeResultCorrect, KleeResultIncorrect, KleeResultUnknown]:
        verification_result_type_to_info[t] = []
        verification_result_type_to_benchmark[t] = []

    # Match spec result map. Maps type to
    # tuple (<identifier>, <result>)
    match_spec_result_type_to_info = dict() # results on per task basis
    for t in [ KleeResultMatchSpec, KleeResultMismatchSpec, KleeResultUnknownMatchSpec]:
        match_spec_result_type_to_info[t] = []

    error_runs = []
    num_raw_results = 0
    try:
        # FIXME: Don't use raw form
        resultInfos = KleeRunner.ResultInfo.loadRawResultInfos(args.result_info_file)
        for index, result in enumerate(resultInfos["results"]):
            if 'error' in result:
                _logger.error('Found error result :{}'.format(pprint.pformat(result)))
                error_runs.append(result)
                if args.ignore_error_runs:
                    continue
                else:
                    return 1
            identifier = '{} ({})'.format(
                result["invocation_info"]["program"],
                result["klee_dir"]
            )
            # Load the spec
            spec = kleeanalysis.analyse.load_spec(
                kleeanalysis.analyse.get_augmented_spec_file_path(result))

            if len(args.categories) > 0:
                # FIXME: fp-bench specific
                # Only process the result if the categories of the benchmark
                # are a superset of the requested categories.
                requested_categories = set(args.categories)
                benchmark_categories = set(spec['categories'])
                if not benchmark_categories.issuperset(requested_categories):
                    _logger.warning('Skipping "{}" due to {} not being a superset of {}'.format(
                        identifier,
                        benchmark_categories,
                        requested_categories)
                    )
                    continue
                else:
                    _logger.debug('Keeping "{}" due to {} being a superset of {}'.format(
                        identifier,
                        benchmark_categories,
                        requested_categories)
                    )
            num_raw_results += 1

            outcomes, klee_dir = kleeanalysis.analyse.get_run_outcomes(result)
            assert isinstance(outcomes, list)
            assert len(outcomes) > 0
            warning_msg = ""
            if len(outcomes) > 1:
                warning_msg = 'Multiple outcomes for "{}":\n'.format(identifier)
                multipleOutcomes.append(outcomes)
            for item in outcomes:
                assert isinstance(item, kleeanalysis.analyse.SummaryType)
                summaryCounters[item.code] += 1
                if item.code == KleeRunnerResult.BAD_EXIT:
                    warning_msg += "{} terminated with exit code {}\n".format(
                        identifier,
                        item.payload)
                elif item.code == KleeRunnerResult.OUT_OF_MEMORY:
                    warning_msg += "{} killed due to running out of memory\n".format(
                            identifier)
                elif item.code == KleeRunnerResult.OUT_OF_TIME:
                    timeout_type = item.payload
                    warning_msg += "{} hit timeout ({})\n".format(
                            identifier, timeout_type)
                elif item.code == KleeRunnerResult.INVALID_KLEE_DIR:
                    warning_msg += "{} has an invalid klee directory\n".format(
                        identifier)
                elif item.code == KleeRunnerResult.LOST_TEST_CASE:
                    number_of_lost_tests = item.payload
                    warning_msg += "{} lost {} test case(s)\n".format(
                        identifier,
                        number_of_lost_tests)
                elif item.code == KleeRunnerResult.EXECUTION_ERRORS:
                    execution_errors = item.payload
                    warning_msg += "{} had execution errors during exploration:\n{}\n".format(
                        identifier,
                        kleeanalysis.analyse.show_failures_as_string(execution_errors)
                    )
                elif item.code == KleeRunnerResult.USER_ERRORS:
                    user_errors = item.payload
                    warning_msg += "{} had user errors during exploration:\n{}\n".format(
                        identifier,
                        kleeanalysis.analyse.show_failures_as_string(user_errors))
                elif item.code == KleeRunnerResult.MISC_ERRORS:
                    misc_errors = item.payload
                    warning_msg += "{} had misc errors during exploration:\n{}\n".format(
                        identifier,
                        kleeanalysis.analyse.show_failures_as_string(misc_errors))
                elif item.code == KleeRunnerResult.VALID_KLEE_DIR:
                    # We have a useful klee directory
                    pass
                else:
                    raise Exception("Unhandled KleeRunnerResult")

            # Finally display warnings if any.
            if len(warning_msg) > 0:
                _logger.warning(warning_msg)

            # Check what the verification verdicts of KLEE are for
            # the fp-bench tasks.
            verification_results = get_klee_verification_results_for_fp_bench(klee_dir, allow_invalid_klee_dir=args.allow_invalid_klee_dir)

            # Update results on per task basis
            for vr in verification_results:
                verification_result_type_to_info[type(vr)].append((identifier, vr))

            # Update results on per benchmark basis
            summary_result = get_klee_dir_verification_summary_across_tasks(verification_results)
            verification_result_type_to_benchmark[type(summary_result)].append(identifier)

            # Compare to the spec
            spec_match_results = []
            for vr in verification_results:
                spec_match_result = kleeanalysis.analyse.match_klee_verification_result_against_spec(
                    vr,
                    spec
                )
                spec_match_results.append(spec_match_result)
                # Update results for spec comparision (on per task basis)
                match_spec_result_type_to_info[type(spec_match_result)].append(
                    (identifier, spec_match_result))

            # Show warnings if necessary
            report_spec_matches(identifier, spec_match_results)

    except KeyboardInterrupt:
        _logger.info('Received KeyboardInterrupt')
        exitCode = 1

    print("")
    print('# of raw results: {}'.format(num_raw_results))
    for name , value in sorted(summaryCounters.items(), key=lambda i: i[0].name):
        print("# of {}: {}".format(name, value))

    if len(multipleOutcomes) > 0:
        _logger.warning('{} benchmark(s) had multiple outcomes'.format(len(multipleOutcomes)))

    print("")
    sanityCheckCount = 0
    print('=== Verification counts per benchmark ===')
    for t in [ KleeResultCorrect, KleeResultIncorrect, KleeResultUnknown]:
        print('# of {}: {}'.format(t.__name__,
            len(verification_result_type_to_benchmark[t])))
        sanityCheckCount += len(verification_result_type_to_benchmark[t])
    if len(args.categories) == 0:
        # Assert doesn't make sense when we skip
        assert sanityCheckCount == len(resultInfos["results"])

    print("")
    sanityCheckCountTotal = 0
    print('=== Verification counts by task ===')
    for t in [ KleeResultCorrect, KleeResultIncorrect, KleeResultUnknown]:
        print('# of {}: {}'.format(t.__name__,
            len(verification_result_type_to_info[t])))
        sanityCheckCountTotal += len(verification_result_type_to_info[t])

        # Provide per task break down.
        taskCount = dict()
        for identifier, vr in verification_result_type_to_info[t]:
            try:
                taskCount[vr.task] += 1
            except KeyError:
                taskCount[vr.task] = 1
        sanityCheckCountTask = 0
        for task, count in sorted(taskCount.items(), key=lambda tup: tup[0]):
            print('  # of task {}: {}'.format(task, count))
            sanityCheckCountTask += count

            # HACK: To dump program names. Remove me!
            if args.dump_verified_incorrect_no_assert_fail:
                if t is KleeResultIncorrect and task == 'no_assert_fail':
                    for d in sorted(verification_result_type_to_info[t], key=lambda k: k[0]):
                        id = d[0].split(' ')[0]
                        print(os.path.basename(id))
        assert sanityCheckCountTask == len(verification_result_type_to_info[t])

        # Report counts of the reasons we report unknown
        if t == KleeResultUnknown:
            print('  Reasons for reporting unknown')
            unknownReasonCount = dict()
            for identifier, vr in verification_result_type_to_info[t]:
                count += 1
                try:
                    unknownReasonCount[vr.reason].append((identifier,vr))
                except KeyError:
                    unknownReasonCount[vr.reason] = [(identifier,vr)]
            for reason, idens_vrs in sorted(unknownReasonCount.items(), key=lambda tup: tup[0]):
                print('    # because "{}": {}'.format(reason, len(idens_vrs)))
                # Report early termination reasons
                if reason == KleeResultUnknownReason.EARLY_TERMINATION:
                    earlyTermReasonCount = dict()
                    seenTestCases = set()
                    for _, vr in idens_vrs:
                        for test_case in vr.test_cases:
                            if test_case.ktest_file in seenTestCases:
                                # Make sure we record a test case only once.
                                continue
                            assert test_case.early is not None
                            early_term_message = (' '.join(test_case.early.message)).strip()
                            try:
                                earlyTermReasonCount[early_term_message] += 1
                            except KeyError:
                                earlyTermReasonCount[early_term_message] = 1
                            seenTestCases.add(test_case.ktest_file)
                    for early_termination_reason,count in sorted(earlyTermReasonCount.items(), key=lambda k: k[0]):
                        print("      # terminated early because \"{}\": {} unique path(s) across {} tasks".format(
                            early_termination_reason,
                            count,
                            len(idens_vrs)))

    if len(args.categories) == 0:
        # Don't sanity check if we do skipping
        assert sanityCheckCountTotal == (len(resultInfos["results"])*len(kleeanalysis.verificationtasks.fp_bench_tasks))

    print("")
    print("# of total tasks: {} ({} * {})".format(
        sanityCheckCountTotal,
        num_raw_results,
        len(kleeanalysis.verificationtasks.fp_bench_tasks))
    )
    print("")

    print('=== Spec matches by task ===')
    # Report spec matches/mismatches/unknowns
    for ty, result_tuples in sorted(match_spec_result_type_to_info.items(), key=lambda t: str(t[0])):
        print('# of {}: {}'.format(ty.__name__, len(result_tuples)))

        if ty == KleeResultMatchSpec:
            # Break down by type
            match_as_correct = list(filter(lambda tup: tup[1].expect_correct, result_tuples))
            match_as_incorrect = list(filter(lambda tup: tup[1].expect_correct is False,
                result_tuples))
            print('  # of correct: {}'.format(len(match_as_correct)))
            print('  # of incorrect: {}'.format(len(match_as_incorrect)))
        elif ty == KleeResultMismatchSpec:
            # Break down by reason
            mismatch_reasons = dict()
            # Manually add a few reasons we always want to see with a count of zero so they
            # always show in the output
            for reason in [ KleeMatchSpecReason.EXPECT_CORRECT_KLEE_REPORTS_INCORRECT,
                KleeMatchSpecReason.EXPECT_INCORRECT_KLEE_REPORTS_CORRECT,
                KleeMatchSpecReason.DISALLOWED_CEX]:
                mismatch_reasons[reason] = []
            for identifier, mismatch in result_tuples:
                assert isinstance(mismatch, KleeResultMismatchSpec)
                try:
                    id_list = mismatch_reasons[mismatch.reason]
                    id_list.append('{} {}'.format(mismatch.task, identifier))
                except KeyError:
                    mismatch_reasons[mismatch.reason] = ['{} {}'.format(mismatch.task, identifier)]
            for reason, identifiers in sorted(mismatch_reasons.items(), key=lambda p: p[0]):
                print(' # because "{}": {}'.format(reason, len(identifiers)))
                if args.dump_spec_mismatches:
                    for id in identifiers:
                        print("MISMATCH: {}".format(id))
        elif ty == KleeResultUnknownMatchSpec:
            # Break down by reason
            unknown_reasons = dict()
            print("  By reason:")
            for identifier, unknown in result_tuples:
                assert isinstance(unknown, KleeResultUnknownMatchSpec)
                try:
                    id_list = unknown_reasons[unknown.reason]
                    id_list.append(identifier)
                except KeyError:
                    unknown_reasons[unknown.reason] = [identifier]
            for reason, ids in sorted(unknown_reasons.items(), key=lambda p: p[0]):
                print('   # because "{}": {}'.format(reason, len(ids)))
            print('  By expected correctness:')
            # Break down by expected correctness
            match_as_correct = list(filter(lambda tup: tup[1].expect_correct is True,
                result_tuples))
            match_as_incorrect = list(filter(lambda tup: tup[1].expect_correct is False,
                result_tuples))
            match_as_unknown = list(filter(lambda tup: tup[1].expect_correct is None,
                result_tuples))
            print('    # of expect correct: {}'.format(len(match_as_correct)))
            print('    # of expect incorrect: {}'.format(len(match_as_incorrect)))
            if args.dump_spec_match_unknown_expect_incorrect:
                for id, result_info in match_as_incorrect:
                    print("EXPECT INCORRECT: {} {}".format(result_info.task, id))
            print('    # of expect unknown: {}'.format(len(match_as_unknown)))



    return exitCode

def report_spec_matches(identifier, spec_match_results):
    assert isinstance(identifier, str)
    assert isinstance(spec_match_results, list)
    assert len(spec_match_results) > 0
    for spec_match_result in spec_match_results:
        if isinstance(spec_match_result, KleeResultMatchSpec):
            if len(spec_match_result.warnings) > 0:
                msg = 'MATCH SPEC for task {} but with warnings:\n{}\n'.format(
                    spec_match_result.task,
                    identifier)
                for (warning_msg, data) in spec_match_result.warnings:
                    if warning_msg == kleeanalysis.analyse.KleeMatchSpecWarnings.CEX_NOT_IN_SPEC:
                        test_cases = data
                        msg += "{}\n{}\n".format(warning_msg,
                            kleeanalysis.analyse.show_failures_as_string(test_cases))
                    elif warning_msg == kleeanalysis.analyse.KleeMatchSpecWarnings.NOT_ALL_CEX_OBSERVED:
                        msg += "{}. The following locations were not covered:\n{}\n".format(warning_msg, data)
                    else:
                        raise Exception('Unhandled warning')
                    
                _logger.warning(msg)
            else:
                _logger.debug('MATCH SPEC for task {}:\n{}\n'.format(
                    spec_match_result.task,
                    identifier)
                )
        elif isinstance(spec_match_result, KleeResultMismatchSpec):
            test_cases_as_str=""
            # Avoid trying to print test cases that might be succesful test cases
            # (i.e. are not error test cases)
            if spec_match_result.reason != KleeMatchSpecReason.EXPECT_INCORRECT_KLEE_REPORTS_CORRECT:
                test_cases_as_str = "\n{}\n".format(
                    kleeanalysis.analyse.show_failures_as_string(
                        spec_match_result.test_cases)
                )
            _logger.warning('MISMATCH SPEC for task {}:\n{}\n{}\n{}'.format(
                spec_match_result.task,
                identifier,
                spec_match_result.reason,
                test_cases_as_str
                )
            )
        else:
            assert isinstance(spec_match_result, KleeResultUnknownMatchSpec)
            _logger.debug('UNKNOWN MATCH for task {}:\n{}\n{}\n'.format(
                spec_match_result.task,
                identifier,
                spec_match_result.reason)
            )

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
