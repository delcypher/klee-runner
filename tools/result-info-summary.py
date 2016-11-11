#!/usr/bin/env python
# vim: set sw=4 ts=4 softtabstop=4 expandtab:
"""
Perform verification of a klee-runner result yaml file and associated working
directory.
"""

import argparse
import logging
from enum import Enum
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
    KleeResultUnknownMatchSpec

_logger = logging.getLogger(__name__)

def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--result-info-file",
                        dest="result_info_file",
                        help="result info file. (Default stdin)",
                        type=argparse.FileType('r'),
                        default=sys.stdin)
    DriverUtil.parserAddLoggerArg(parser)

    args = parser.parse_args(args=argv)
    DriverUtil.handleLoggerArgs(args)

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

    try:
        # FIXME: Don't use raw form
        resultInfos = KleeRunner.ResultInfo.loadRawResultInfos(args.result_info_file)
        for index, result in enumerate(resultInfos["results"]):
            identifier = '{} ({})'.format(
                result["invocation_info"]["program"],
                result["klee_dir"]
            )
            outcomes, klee_dir = kleeanalysis.analyse.get_run_outcomes(result)
            assert isinstance(outcomes, list)
            assert len(outcomes) > 0
            if len(outcomes) > 1:
                _logger.warning('Multiple outcomes for "{}"'.format(identifier))
                multipleOutcomes.append(outcomes)
            for item in outcomes:
                assert isinstance(item, kleeanalysis.analyse.SummaryType)
                summaryCounters[item.code] += 1
                if item.code == KleeRunnerResult.BAD_EXIT:
                    _logger.warning("{} terminated with exit code {}".format(
                        identifier,
                        item.payload))
                elif item.code == KleeRunnerResult.OUT_OF_MEMORY:
                    _logger.warning("{} killed due to running out of memory".format(
                            identifier))
                elif item.code == KleeRunnerResult.OUT_OF_TIME:
                    _logger.warning("{} hit timeout".format(
                            identifier))
                elif item.code == KleeRunnerResult.INVALID_KLEE_DIR:
                    _logger.warning("{} has an invalid klee directory".format(
                        identifier))
                elif item.code == KleeRunnerResult.VALID_KLEE_DIR:
                    # We have a useful klee directory
                    pass
                else:
                    raise Exception("Unhandled KleeRunnerResult")

            # Check what the verification verdicts of KLEE are for
            # the fp-bench tasks.
            verification_results = get_klee_verification_results_for_fp_bench(klee_dir)

            # Update results on per task basis
            for vr in verification_results:
                verification_result_type_to_info[type(vr)].append((identifier, vr))

            # Update results on per benchmark basis
            summary_result = get_klee_dir_verification_summary_across_tasks(verification_results)
            verification_result_type_to_benchmark[type(summary_result)].append(identifier)

            # Compare to the spec
            spec = kleeanalysis.analyse.load_spec(
                kleeanalysis.analyse.get_augmented_spec_file_path(result))
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
    print('# of raw results: {}'.format(len(resultInfos["results"])))
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
        assert sanityCheckCountTask == len(verification_result_type_to_info[t])

        # Report counts of the reasons we report unknown
        if t == KleeResultUnknown:
            print('  Reasons for reporting unknown')
            unknownReasonCount = dict()
            for identifier, vr in verification_result_type_to_info[t]:
                count += 1
                try:
                    unknownReasonCount[vr.reason] += 1
                except KeyError:
                    unknownReasonCount[vr.reason] = 1
            for reason, count in sorted(unknownReasonCount.items(), key=lambda tup: tup[0]):
                print('    # because "{}": {}'.format(reason, count))

    assert sanityCheckCountTotal == (len(resultInfos["results"])*len(kleeanalysis.verificationtasks.fp_bench_tasks))

    print("")
    print("# of total tasks: {} ({} * {})".format(
        sanityCheckCountTotal,
        len(resultInfos["results"]),
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
            for _, mismatch in result_tuples:
                assert isinstance(mismatch, KleeResultMismatchSpec)
                try:
                    mismatch_reasons[mismatch.reason] += 1
                except KeyError:
                    mismatch_reasons[mismatch.reason] = 1
            for reason, count in sorted(mismatch_reasons.items(), key=lambda p: p[0]):
                print(' # because "{}": {}'.format(reason, count))
        elif ty == KleeResultUnknownMatchSpec:
            # Break down by reason
            unknown_reasons = dict()
            print("  By reason:")
            for _, unknown in result_tuples:
                assert isinstance(unknown, KleeResultUnknownMatchSpec)
                try:
                    unknown_reasons[unknown.reason] += 1
                except KeyError:
                    unknown_reasons[unknown.reason] = 1
            for reason, count in sorted(unknown_reasons.items(), key=lambda p: p[0]):
                print('   # because "{}": {}'.format(reason, count))
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
                for (warning_msg, test_cases) in spec_match_result.warnings:
                    msg += "{}\n{}\n".format(warning_msg,
                        kleeanalysis.analyse.show_failures_as_string(test_cases))
                    
                _logger.warning(msg)
            else:
                _logger.debug('MATCH SPEC for task {}:\n{}\n'.format(
                    spec_match_result.task,
                    identifier)
                )
        elif isinstance(spec_match_result, KleeResultMismatchSpec):
            _logger.warning('MISMATCH SPEC for task {}:\n{}\n'.format(
                spec_match_result.task,
                identifier,
                spec_match_result.reason)
            )
            _logger.warning(
                kleeanalysis.analyse.show_failures_as_string(spec_match_result.test_cases))
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