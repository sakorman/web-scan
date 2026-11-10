import pytest
from scripts.benchmark_gmdepth_targets import aggregate, target_indices


def test_target_selection_uses_full_names_and_input_order():
    pairs = [{'target': {'name': s}} for s in ['later.jpg','first.jpg','later.jpg']]
    inputs = [{'name': s} for s in ['first.jpg','unused.jpg','later.jpg']]
    assert target_indices(pairs, inputs) == [0,2]
    with pytest.raises(ValueError, match='identity'):
        target_indices(pairs, inputs[:2])
    with pytest.raises(ValueError, match='identity'):
        target_indices(pairs, inputs+inputs[:1])


def test_repeated_target_counts_once_and_unmatched_is_not_zero_error():
    def row(k, a, b):
        def score(x):
            return {'valid_prediction_metrics': {'mean_abs_rel': x}}
        return dict(target=k, status='scored', shared=score(a), independent=score(b))
    result = aggregate([row('a', .2, .4), row('a', .6, .2), row('b', .2, .4),
                        dict(target='c', status='unmatched')])
    assert (result['pairs'],result['targets'],result['improved']) == (3,2,1)
    assert result['shared'] == pytest.approx(.3)
    assert result['independent'] == pytest.approx(.35)


def test_missing_truth_is_not_a_perfect_prediction():
    assert aggregate([dict(status='unmatched')]) == {'targets': 0, 'pairs': 0}
    empty = {'valid_prediction_metrics': None}
    with pytest.raises(ValueError, match='No valid sensor'):
        aggregate([dict(status='scored',target='a',shared=empty,independent=empty)])
