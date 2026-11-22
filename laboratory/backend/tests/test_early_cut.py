"""Pure tests of layer semantics and the Max-Sum adapter contract."""
from copy import deepcopy
import unittest

from adaptive_search.early_cut.signals import LocalSignal, PossibleMessage, maxsum_warning


def fixture():
    configuration = dict(algorithm='homework', horizon_ms=60000,
        defaults=dict(delay_ms=100, delay_jitter_ms=20, interval_ms=1500, interval_jitter_ms=0),
        nodes=[dict(id='s', initial=0), dict(id='r', initial=1)], edges=[dict(source='s', target='r')])
    def message(ident, value, due):
        return dict(id=ident, receiver='r', value=value, due_ms=due)
    # r's next update at 1000. Eight committed low values would overshoot.
    batch = [message(f'known-{i}', 0, 850) for i in range(7)]
    state = dict(time_ms=880, counters={'delay:s>r':8}, last_due={},
        agents={'r':dict(z=1, phase='collecting', batch=batch, inbox=[], next_update_ms=1000),
                's':dict(z=0, phase='waiting', batch=[], inbox=[], next_update_ms=1700)},
        pending={'equal':message('equal', 1, 1000)},
        timers={'send':dict(kind='cycle_start', entity='s', time_ms=900),
                'receive_update':dict(kind='update', entity='r', time_ms=1000),
                'source_update':dict(kind='update', entity='s', time_ms=1700)})
    return configuration, state


class LocalCut(unittest.TestCase):
    def test_possible_send_crosses_exact_closure(self):
        c,s = fixture()
        original = deepcopy(s)
        row = LocalSignal(c).observe(s)[0]
        self.assertEqual(s, original)
        self.assertEqual(row['batch_projection'], 0)
        self.assertEqual(row['committed_pending_ids'], [])
        self.assertAlmostEqual(row['projection_range'][0], -1/7)
        self.assertEqual(row['projection_range'][1], 0)
        self.assertEqual(row['intervention_opportunities'][0]['last_on_time_delay_ms'], 99)
        self.assertEqual(row['intervention_opportunities'][0]['ordinal'], 8)
        self.assertTrue(row['prediction_closed'])
        self.assertTrue(row['actionable'])

    def test_fifo_makes_send_always_late_and_pending_stays_fixed(self):
        c,s = fixture()
        s['last_due']['s>r'] = 1000
        s['pending']['early'] = dict(id='early', receiver='r', value=0, due_ms=999)
        row = LocalSignal(c).observe(s)[0]
        self.assertTrue(row['warning'])
        self.assertAlmostEqual(row['committed_projection'], -1/7)
        self.assertEqual(row['committed_pending_ids'], ['early'])
        self.assertEqual(row['intervention_opportunities'], [])
        self.assertFalse(row['actionable'])

    def test_future_source_update_leaves_projection_open(self):
        c,s = fixture()
        s['agents']['s'].update(phase='collecting', next_update_ms=890)
        s['timers']['source_update']['time_ms'] = 890
        del s['timers']['send']
        c['nodes'][0]['overrides'] = dict(interval_ms=810)
        row = LocalSignal(c).observe(s)[0]
        self.assertFalse(row['prediction_closed'])
        self.assertEqual(row['unmodeled_future_sends'][0]['earliest_due_ms'], 980)
        self.assertFalse(row['actionable'])

    def test_explicit_delay_and_episode_remove_optional_membership(self):
        c,s = fixture()
        c['schedule'] = dict(delays={'s>r':[99]*9})
        row = LocalSignal(c).observe(s)[0]
        self.assertTrue(row['warning'])
        self.assertEqual(row['guaranteed_future_count'], 1)
        self.assertFalse(row['actionable'])
        c['schedule'] = None
        c['episodes'] = [dict(kind='link_slowdown', targets=['s>r'], factor=2, start_ms=900, duration_ms=10)]
        row = LocalSignal(c).observe(s)[0]
        self.assertFalse(row['warning'])

    def test_waiting_uses_inbox_and_partial_layer_is_rejected(self):
        c,s = fixture()
        a=s['agents']['r'];a.update(phase='waiting', inbox=a['batch'], batch=[])
        self.assertEqual(LocalSignal(c).observe(s)[0]['batch_count'],7)
        s['timers']['send']['time_ms']=s['time_ms']
        with self.assertRaisesRegex(ValueError,'partially processed'):
            LocalSignal(c).observe(s)


class MaxSumCut(unittest.TestCase):
    def evaluate(self, scores, message, **kwargs):
        return maxsum_warning(scores,message,now_ms=800,decision_ms=1000,**kwargs)

    def test_cost_gap_and_new_message_flip_including_equal_time_delivery(self):
        row=self.evaluate([0,2,8],PossibleMessage([3,0,0],990,1000))
        self.assertEqual((row['gap'],row['winner'],row['winner_if_received']),(2,0,1))
        self.assertTrue(row['actionable'])
        self.assertFalse(self.evaluate([0,2,8],PossibleMessage([3,0,0],1000,1002))['actionable'])

    def test_fixed_arrival_and_constant_shift(self):
        row=self.evaluate([0,2,8],PossibleMessage([3,0,0],995,995,decision_pending=False))
        self.assertTrue(row['winner_changes_if_received'])
        self.assertFalse(row['actionable'])
        row=self.evaluate([0,2,8],PossibleMessage([6,6,6],990,1100))
        self.assertFalse(row['actionable'])

    def test_tie_and_sign_duality(self):
        message=PossibleMessage([2,0,0],990,1100)
        row=self.evaluate([0,0,10], message)
        self.assertEqual(row['winner'],0)
        self.assertEqual(row,self.evaluate([0,0,-10],PossibleMessage([-2,0,0],990,1100),maximize=True))
        self.assertEqual(self.evaluate([0,-.0001,6],None,tie_atol=.001)['winner'],0)

    def test_invalid_and_already_resolved(self):
        with self.assertRaisesRegex(ValueError,'finite'):
            self.evaluate([float('nan'),1,2],None)
        with self.assertRaisesRegex(ValueError,'exact arrival'):
            self.evaluate([1,0,2],PossibleMessage([0,0,0],990,1100,decision_pending=False))
        with self.assertRaisesRegex(ValueError,'after'):
            self.evaluate([1,0,2],PossibleMessage([0,0,0],800,1000))


if __name__ == '__main__':
    unittest.main()
