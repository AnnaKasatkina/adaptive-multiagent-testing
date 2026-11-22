from itertools import permutations, product
from math import inf, nan
import unittest

from adaptive_search.fuzz_feedback.signals import (LocalWindow, Message, Tolerance, local_update_token, maxsum_signal,
                      observe_local_transfer, predict_local_transfer, selection_key)


class LocalSignalTests(unittest.TestCase):
    def setUp(self):
        self.msg = Message('m', 'A', 7)
        self.other = Message('n', 'C', 7)
        self.old = (LocalWindow(1000, 0, (self.msg,)), LocalWindow(2000, 1, (self.other,)))
        self.new = (LocalWindow(1000, 0, ()), LocalWindow(2000, 0, (self.other, self.msg)))
        self.kw = dict(mean=3.5, tolerance=Tolerance(7))

    def test_cancellation_and_neutral_category(self):
        forecast = -1.2857142857142856 + 2/7
        self.assertGreater(forecast, -1)
        token = local_update_token(0, -1, forecast, **self.kw)
        self.assertEqual(token, (0, 0, 0, 'neutral'))
        self.assertEqual(token, local_update_token(0, -1, -1, **self.kw))

    def test_later_transfer_with_strict_same_time_boundary(self):
        result = predict_local_transfer(self.old, self.msg, old_due_ms=999, new_due_ms=1000, **self.kw)
        self.assertEqual((result.source_ms, result.destination_ms), (1000, 2000))
        removed, added = result.effects
        self.assertEqual((removed.old_batch_ids, removed.new_batch_ids), (('m',), ()))
        self.assertEqual((added.old_batch_ids, added.new_batch_ids), (('n',), ('n','m')))
        self.assertEqual((removed.old_message_contribution, added.new_message_contribution), (1, 1))
        self.assertEqual(removed.state_effect, -1)
        self.assertAlmostEqual(added.state_effect, 2 - 13/7)
        observed = observe_local_transfer(self.old, self.new, [1, 13/7], [0, 2],
                                          message_id='m', **self.kw)
        self.assertEqual(result.token, observed.token)

    def test_earlier_transfer_reverses_the_two_state_effects(self):
        result = predict_local_transfer(self.new, self.msg, old_due_ms=1000, new_due_ms=999, **self.kw)
        source, destination = result.effects
        self.assertEqual((source.time_ms, destination.time_ms), (2000, 1000))
        self.assertAlmostEqual(source.state_effect, 13/7 - 2)
        self.assertEqual(destination.state_effect, 1)

    def test_recurrence_between_nonadjacent_windows(self):
        midpoint = LocalWindow(2000, 1, (Message('k', 'C', 1),))
        final = LocalWindow(3000, 1, (self.other,))
        result = predict_local_transfer((self.old[0], midpoint, final), self.msg,
                                        old_due_ms=999, new_due_ms=2000, **self.kw)
        # After losing m the difference is -1, then -6/7 at the middle update.
        self.assertAlmostEqual(result.effects[1].new_before, 1/7)
        self.assertAlmostEqual(result.effects[1].state_effect, 12/49)

    def test_same_window_and_zero_contribution_have_no_novelty(self):
        same = predict_local_transfer(self.old, self.msg, old_due_ms=999, new_due_ms=998, **self.kw)
        self.assertFalse(same.transferred)
        self.assertIsNone(same.token)
        zero = Message('m', 'A', 0)
        windows = (LocalWindow(1000, 0, (zero,)), LocalWindow(2000, 0, ()))
        moved = predict_local_transfer(windows, zero, old_due_ms=999, new_due_ms=1000, **self.kw)
        self.assertTrue(moved.transferred)
        self.assertIsNone(moved.token)
        self.assertEqual(moved.effects[0].new_batch_ids, ())

    def test_missing_window_or_wrong_message_is_unscorable(self):
        for windows, due in ((self.old, 2000), (tuple(reversed(self.old)), 1000),
                             (self.new, 1000)):
            with self.subTest(windows=windows, due=due), self.assertRaises(ValueError):
                predict_local_transfer(windows, self.msg, old_due_ms=999, new_due_ms=due, **self.kw)


class MaxSumSignalTests(unittest.TestCase):
    def setUp(self):
        self.tolerance = Tolerance(10)

    def score(self, base, old, new, bound=5):
        return maxsum_signal(base, old, new, contribution_bound=bound, tolerance=self.tolerance)

    def test_bounded_transfer_changes_winner_and_margin(self):
        result = self.score({'A': 1, 'B': 0, 'C': -2}, {'A': 0, 'B': 0, 'C': 0},
                            {'A': 0, 'B': 3, 'C': 0})
        self.assertEqual((result.old_choice, result.new_choice), ('A', 'B'))
        self.assertEqual((result.old_margin, result.new_margin), (1, 2))
        self.assertEqual(dict(result.new_relative_scores), {'A': 0, 'B': 2, 'C': -3})
        self.assertTrue(result.robust_flip)
        self.assertFalse(result.stable_certificate)

    def test_tie_break_and_input_order(self):
        rows = [('Z', 2), ('B', 2), ('A', 2 - 1e-12)]
        for order in permutations(rows):
            old = dict.fromkeys(dict(order), 0)
            result = self.score(dict(order), old, old)
            self.assertEqual(result.old_choice, 'B')
            self.assertEqual(result.old_tolerant_choice, 'A')
            self.assertEqual(result.old_near_best, ('A', 'B', 'Z'))
            self.assertEqual(result.old_margin, 0)
            self.assertIsNone(result.token)

    def test_gauge_shift_and_near_zero_jitter_do_not_add_novelty(self):
        base, old = {'A': 1, 'B': 1}, {'A': 0, 'B': 0}
        shifted = self.score(base, old, {'A': 3, 'B': 3})
        self.assertEqual(shifted.perturbation_span, 0)
        self.assertIsNone(shifted.token)
        jitter = self.score(base, old, {'A': 0, 'B': 1e-12})
        self.assertTrue(jitter.choice_changed)
        self.assertFalse(jitter.robust_flip)
        self.assertEqual(jitter.new_tolerant_choice, 'A')
        self.assertIsNone(jitter.token)
        self.assertEqual(selection_key(shifted, {None: 10}, 4), selection_key(jitter, {}, 4))

    def test_stability_certificate_on_grid_of_perturbations(self):
        options = ('A', 'B', 'C')
        certified = 0
        for a, b, c in product((-2, -1, 0, 1, 2), repeat=3):
            base = dict(zip(options, (a,b,c)))
            old = dict.fromkeys(options, 0)
            for values in product((-1,0,1), repeat=3):
                new = dict(zip(options, values))
                result = self.score(base, old, new, bound=1)
                if result.stable_certificate:
                    certified += 1
                    scores = {k: base[k] + new[k] for k in options}
                    self.assertTrue(all(scores[result.old_choice] > scores[k]
                                        for k in options if k != result.old_choice))
        self.assertGreater(certified, 0)

    def test_invalid_domains_bounds_and_nonfinite_inputs(self):
        a, b = {'A': 0, 'B': 0}, {'A': 0, 'B': 1}
        for base, old, new in ((a,a,{'A':0}), (a,a,{'A':0,'B':6}),
                               (a,a,{'A':0,'B':nan}), ({'A':inf,'B':0},a,b),
                               ({'A':0,1:0},a,b)):
            with self.subTest(base=base, new=new), self.assertRaises(ValueError):
                self.score(base, old, new)

    def test_key_prefers_real_unseen_transfer_to_seen_and_neutral(self):
        base, zero = {'A': 1, 'B': 0}, {'A': 0, 'B': 0}
        neutral = self.score(base, zero, zero)
        flip = self.score(base, zero, {'A': -2, 'B': 0})
        same = self.score(base, zero, {'A': 2, 'B': 0})
        archive = {flip.token: 2}
        self.assertLess(selection_key(same, archive, 3), selection_key(flip, archive, 0))
        self.assertLess(selection_key(flip, archive, 0), selection_key(neutral, archive, 1))


if __name__ == '__main__':
    unittest.main()
