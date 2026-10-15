from kiwi_pi.scan import _consolidate_detections


def detection(object_id, box, score):
    return {'id':object_id,'box':box,'detector_score':score,'detector_label':'instrument'}


def test_real_multimeter_proposals_reduce_to_whole_instrument():
    # Captured DINO proposals from the user's clear multimeter image. Internal
    # parts otherwise keep competing for the whole instrument's temporal track.
    proposals = [
        detection(1,[109,130,525,911],.461),
        detection(2,[5,5,715,955],.427),
        detection(3,[113,140,512,406],.364),
        detection(4,[205,473,423,687],.334),
        detection(5,[131,404,506,902],.333),
        detection(6,[190,229,430,369],.319),
        detection(7,[210,479,417,613],.303),
        detection(8,[169,435,462,724],.286),
        detection(9,[130,715,505,905],.273),
    ]
    assert [item['id'] for item in _consolidate_detections(proposals,{})] == [1]


def test_separate_objects_are_preserved_and_scores_decide_duplicates():
    proposals = [detection(1,[0,0,100,100],.4),
                 detection(2,[3,3,103,103],.8),
                 detection(3,[150,0,250,100],.6)]
    assert [item['id'] for item in _consolidate_detections(proposals,{})] == [2,3]


def test_containment_policy_can_be_disabled_for_nested_object_scenes():
    proposals = [detection(1,[0,0,100,100],.8),detection(2,[20,20,40,40],.6)]
    assert len(_consolidate_detections(proposals,{'detection_containment':1.01})) == 2
