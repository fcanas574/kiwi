"""Small, geometry-only tracker for the continuous scan flow."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


_MAX_TRACKS = 12
_DEFAULTS = {
    'association_iou': 0.3,
    'ambiguity_delta': 0.1,
    'stable_samples': 3,
    'stable_seconds': 1.0,
    'stable_iou': 0.6,
    'area_tolerance': 0.15,
    'center_tolerance': 0.02,
    'missing_samples': 3,
    'max_gap_seconds': 10.0,
    'retry_seconds': 5.0,
    'rearm_samples': 3,
}


@dataclass
class _Reservation:
    revision: int
    frame_id: int
    box: tuple[int, int, int, int]
    normalized_box: tuple[float, float, float, float]


@dataclass
class _Track:
    track_id: int
    revision: int
    box: tuple[float, float, float, float]
    pixel_box: tuple[int, int, int, int]
    image_size: tuple[int, int]
    frame_id: int
    last_seen: float
    stable_reference: tuple[float, float, float, float] | None
    stable_count: int
    stable_since: float | None
    missing_count: int = 0
    state: str = 'observing'
    recognition: dict | None = None
    category: str | None = None
    provisional: dict | None = None
    provisional_revision: int | None = None
    provisional_box: tuple[float, float, float, float] | None = None
    provisional_frame_id: int | None = None
    reservation: _Reservation | None = None
    last_attempt_at: float | None = None
    rejected_box: tuple[float, float, float, float] | None = None
    failure_sample_after: int | None = None
    failure_sample_seen: bool = True


class Tracker:
    """Associate detections and gate Qwen results on later compatible samples.

    ``rules`` is the ``scan`` configuration mapping. The tracker only consumes
    detections and externally supplied monotonic timestamps; it has no model,
    network, camera, or GPIO dependencies.
    """

    def __init__(self, rules: dict):
        options = rules.get('scan', rules) if isinstance(rules, dict) else {}
        self.rules = {**_DEFAULTS, **(options or {})}
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self._known_categories: set[str] = set()
        self._visibility: dict[str, bool | None] = {}
        self._last_frame_id: int | None = None

    def update(self, frame_id: int, image_size: tuple[int, int],
               detections: list[dict], now: float) -> list[dict]:
        """Apply a strictly newer DINO sample and confirm compatible Qwen results."""
        if self._last_frame_id is not None and frame_id <= self._last_frame_id:
            return []
        width, height = image_size
        if width <= 0 or height <= 0:
            raise ValueError('image_size must contain positive width and height')

        sample = []
        for detection in (detections or [])[:_MAX_TRACKS]:
            parsed = self._parse_detection(detection, image_size)
            if parsed is not None:
                pixel_box, normalized_box = parsed
                sample.append((pixel_box, normalized_box))

        self._last_frame_id = frame_id
        tracks = list(self._tracks.values())
        scores = [[self._iou(track.box, item[1]) for track in tracks]
                  for item in sample]
        ambiguous_detections: set[int] = set()
        uncertain_tracks: set[int] = set()

        threshold = float(self.rules['association_iou'])
        ambiguity_delta = float(self.rules['ambiguity_delta'])

        # Similar scores for a detection against two tracks mean identity cannot
        # safely be carried through this sample.
        for det_index, row in enumerate(scores):
            candidates = sorted(((score, track_index) for track_index, score in enumerate(row)
                                 if score >= threshold), reverse=True)
            if len(candidates) >= 2 and candidates[0][0] - candidates[1][0] < ambiguity_delta:
                ambiguous_detections.add(det_index)
                uncertain_tracks.update(tracks[index].track_id
                                        for _, index in candidates[:2])

        # The inverse case (two detections competing equally for one track) is
        # uncertain too; the assignment remains one-to-one.
        for track_index, track in enumerate(tracks):
            candidates = sorted(((scores[det_index][track_index], det_index)
                                 for det_index in range(len(sample))
                                 if scores[det_index][track_index] >= threshold), reverse=True)
            if len(candidates) >= 2 and candidates[0][0] - candidates[1][0] < ambiguity_delta:
                uncertain_tracks.add(track.track_id)
                ambiguous_detections.update(index for _, index in candidates[:2])

        edges = sorted(
            ((scores[det_index][track_index], tracks[track_index].track_id,
              track_index, det_index)
             for det_index in range(len(sample))
             for track_index in range(len(tracks))
             if scores[det_index][track_index] >= threshold
             and det_index not in ambiguous_detections
             and tracks[track_index].track_id not in uncertain_tracks),
            key=lambda edge: (-edge[0], edge[1], edge[3]),
        )
        assigned_tracks: dict[int, int] = {}
        assigned_detections: dict[int, int] = {}
        for _, track_id, track_index, det_index in edges:
            if track_id in assigned_tracks or det_index in assigned_detections:
                continue
            assigned_tracks[track_id] = det_index
            assigned_detections[det_index] = track_id

        # A detection that had plausible matches but lost a one-to-one conflict
        # is left unassigned instead of creating a duplicate track.
        for det_index, row in enumerate(scores):
            if det_index in assigned_detections or det_index in ambiguous_detections:
                continue
            plausible = [tracks[index].track_id for index, score in enumerate(row)
                         if score >= threshold]
            if plausible:
                ambiguous_detections.add(det_index)
                uncertain_tracks.update(plausible)

        events: list[dict] = []
        uncertain_categories: set[str] = set()
        definitely_visible: set[str] = set()

        for track in tracks:
            det_index = assigned_tracks.get(track.track_id)
            if det_index is None:
                if track.track_id in uncertain_tracks:
                    # Invalidate any in-flight classification bound to the old
                    # geometry without treating the uncertain sample as absence.
                    track.revision += 1
                    if track.category:
                        uncertain_categories.add(track.category)
                    if track.provisional and track.provisional.get('recognized') is True:
                        category = track.provisional.get('candidate')
                        if isinstance(category, str):
                            uncertain_categories.add(category)
                    track.provisional = None
                    track.provisional_revision = None
                    track.provisional_box = None
                    track.provisional_frame_id = None
                    if track.state == 'provisional':
                        track.state = 'observing'
                    # Plausible presence retains the track and category even
                    # through persistent ambiguity. Only actual unmatched
                    # samples count toward expiry.
                    track.missing_count = 0
                    track.last_seen = now
                else:
                    track.missing_count += 1
                # Retain the reference so motion across a tolerated gap is
                # detected, but require a fresh stable sequence on return.
                track.stable_count = 0
                track.stable_since = None
                if (now - track.last_seen > float(self.rules['max_gap_seconds'])
                        or track.missing_count >= int(self.rules['missing_samples'])):
                    self._tracks.pop(track.track_id, None)
                continue

            pixel_box, normalized_box = sample[det_index]
            previous_last_seen = track.last_seen
            if now - previous_last_seen > float(self.rules['max_gap_seconds']):
                if track.category:
                    uncertain_categories.add(track.category)
                track.revision += 1
                track.state = 'observing'
                track.recognition = None
                track.category = None
                track.provisional = None
                track.provisional_revision = None
                track.provisional_box = None
                track.provisional_frame_id = None
                track.reservation = None
                track.last_attempt_at = None
                track.rejected_box = None
                track.failure_sample_after = None
                track.failure_sample_seen = True
                track.stable_reference = None
                track.stable_count = 0
                track.stable_since = None

            track.missing_count = 0
            if track.stable_reference is None:
                track.stable_reference = normalized_box
                track.stable_count = 1
                track.stable_since = now
            elif self._compatible(track.stable_reference, normalized_box, image_size):
                if track.stable_count == 0:
                    track.stable_since = now
                track.stable_count += 1
            else:
                track.revision += 1
                track.stable_reference = normalized_box
                track.stable_count = 1
                track.stable_since = now
                track.provisional = None
                track.provisional_revision = None
                track.provisional_box = None
                track.provisional_frame_id = None
                if track.state not in ('accepted', 'rejected'):
                    track.state = 'observing'

            track.box = normalized_box
            track.pixel_box = pixel_box
            track.image_size = image_size
            track.frame_id = frame_id
            track.last_seen = now

            if track.failure_sample_after is not None and frame_id > track.failure_sample_after:
                track.failure_sample_seen = True

            if (track.provisional is not None
                    and track.provisional_revision == track.revision
                    and track.provisional_box is not None
                    and track.provisional_frame_id is not None
                    and frame_id > track.provisional_frame_id
                    and self._compatible(track.provisional_box, normalized_box, image_size)):
                recognition = dict(track.provisional)
                if recognition.get('recognized') is True:
                    category = recognition.get('candidate')
                    track.state = 'accepted'
                    track.recognition = recognition
                    track.category = category if isinstance(category, str) else None
                    if track.category:
                        self._known_categories.add(track.category)
                        definitely_visible.add(track.category)
                else:
                    track.state = 'rejected'
                    track.recognition = recognition
                    track.category = None
                    track.rejected_box = track.provisional_box
                events.append({'track_id': track.track_id,
                               'revision': track.revision,
                               'box': list(pixel_box),
                               'recognition': recognition,
                               'frame_id': frame_id})
                track.provisional = None
                track.provisional_revision = None
                track.provisional_box = None
                track.provisional_frame_id = None
                track.reservation = None
            elif track.provisional is not None:
                # The object is still associated, but its current crop no longer
                # matches the crop Qwen classified.
                track.provisional = None
                track.provisional_revision = None
                track.provisional_box = None
                track.provisional_frame_id = None
                track.reservation = None
                if track.state == 'provisional':
                    track.state = 'observing'

            if track.state == 'accepted' and track.category:
                definitely_visible.add(track.category)
            if track.state == 'provisional' and track.provisional:
                if track.provisional.get('recognized') is True:
                    category = track.provisional.get('candidate')
                    if isinstance(category, str):
                        uncertain_categories.add(category)

        # Unassociated, non-ambiguous detections start fresh tracks.
        for det_index, (pixel_box, normalized_box) in enumerate(sample):
            if det_index in assigned_detections or det_index in ambiguous_detections:
                continue
            if len(self._tracks) >= _MAX_TRACKS:
                break
            track = _Track(track_id=self._next_track_id,
                           revision=1,
                           box=normalized_box,
                           pixel_box=pixel_box,
                           image_size=image_size,
                           frame_id=frame_id,
                           last_seen=now,
                           stable_reference=normalized_box,
                           stable_count=1,
                           stable_since=now)
            self._tracks[track.track_id] = track
            self._next_track_id += 1

        self._visibility = {
            category: (True if category in definitely_visible else
                       None if category in uncertain_categories else False)
            for category in self._known_categories
        }
        return events

    def next_candidate(self, now: float) -> dict | None:
        """Reserve the oldest stable, unclassified track for one Qwen request."""
        eligible = [track for track in self._tracks.values()
                    if track.reservation is None
                    and track.provisional is None
                    and track.state != 'accepted'
                    and self._is_stable(track)
                    and self._retry_ready(track, now)]
        if not eligible:
            return None
        track = min(eligible, key=lambda item: (item.stable_since, item.track_id))
        track.reservation = _Reservation(track.revision, track.frame_id,
                                         track.pixel_box, track.box)
        track.state = 'classifying'
        return {'track_id': track.track_id,
                'revision': track.revision,
                'frame_id': track.frame_id,
                'box': list(track.pixel_box)}

    def set_provisional(self, track_id: int, revision: int,
                        recognition: dict, now: float) -> None:
        """Store Qwen's result until DINO supplies a later compatible sample."""
        track = self._tracks.get(track_id)
        if track is None or track.reservation is None:
            return
        reservation = track.reservation
        if reservation.revision != revision:
            return
        track.reservation = None
        if track.revision != revision:
            if track.state == 'classifying':
                track.state = 'observing'
            return
        track.provisional = dict(recognition)
        track.provisional_revision = revision
        track.provisional_box = reservation.normalized_box
        track.provisional_frame_id = reservation.frame_id
        track.last_attempt_at = now
        track.state = 'provisional'

    def classification_failed(self, track_id: int, revision: int, now: float) -> None:
        """Release a request and require a later sample plus cooldown to retry."""
        track = self._tracks.get(track_id)
        if track is None or track.reservation is None:
            return
        if track.reservation.revision != revision:
            return
        track.reservation = None
        track.last_attempt_at = now
        track.failure_sample_after = track.frame_id
        track.failure_sample_seen = False
        if track.state == 'classifying':
            track.state = 'failed'

    def reset(self) -> None:
        """Forget all geometry and classifications for a new scan generation."""
        self._tracks.clear()
        self._next_track_id = 1
        self._known_categories.clear()
        self._visibility.clear()
        self._last_frame_id = None

    def snapshot(self) -> list[dict]:
        """Return a compact copy of current track state for diagnostics/UI use."""
        result = []
        for track in self._tracks.values():
            state = track.state
            if state == 'observing' and self._is_stable(track):
                state = 'stable'
            result.append({'track_id': track.track_id,
                           'revision': track.revision,
                           'box': list(track.pixel_box),
                           'frame_id': track.frame_id,
                           'state': state,
                           'stable_samples': track.stable_count,
                           'stable_since': track.stable_since,
                           'missing_samples': track.missing_count,
                           'category': track.category,
                           'recognition': dict(track.recognition) if track.recognition else None})
        return result

    def category_visibility(self) -> dict[str, bool | None]:
        """Return presence for accepted categories from the latest valid sample."""
        return dict(self._visibility)

    def _retry_ready(self, track: _Track, now: float) -> bool:
        if track.last_attempt_at is not None and now - track.last_attempt_at < float(self.rules['retry_seconds']):
            return False
        if (track.state == 'rejected' and track.rejected_box is not None
                and self._compatible(track.rejected_box, track.box, track.image_size)):
            return False
        if track.failure_sample_after is not None and not track.failure_sample_seen:
            return False
        return True

    def _is_stable(self, track: _Track) -> bool:
        return (track.stable_count >= int(self.rules['stable_samples'])
                and track.stable_since is not None
                and track.last_seen - track.stable_since >= float(self.rules['stable_seconds']))

    @staticmethod
    def _parse_detection(detection: Any, image_size: tuple[int, int]):
        if not isinstance(detection, dict):
            return None
        box = detection.get('box')
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(value) is not int for value in box)):
            return None
        width, height = image_size
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return None
        pixel = (x1, y1, x2, y2)
        return pixel, (x1 / width, y1 / height, x2 / width, y2 / height)

    @staticmethod
    def _area(box: tuple[float, float, float, float]) -> float:
        return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])

    @classmethod
    def _iou(cls, first: tuple[float, float, float, float],
             second: tuple[float, float, float, float]) -> float:
        x1, y1 = max(first[0], second[0]), max(first[1], second[1])
        x2, y2 = min(first[2], second[2]), min(first[3], second[3])
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = cls._area(first) + cls._area(second) - intersection
        return intersection / union if union else 0.0

    def _compatible(self, reference: tuple[float, float, float, float],
                    current: tuple[float, float, float, float],
                    image_size: tuple[int, int]) -> bool:
        if self._iou(reference, current) < float(self.rules['stable_iou']):
            return False
        reference_area = self._area(reference)
        current_area = self._area(current)
        if not reference_area or abs(current_area / reference_area - 1.0) > float(self.rules['area_tolerance']):
            return False
        ref_center = ((reference[0] + reference[2]) / 2,
                      (reference[1] + reference[3]) / 2)
        cur_center = ((current[0] + current[2]) / 2,
                      (current[1] + current[3]) / 2)
        width, height = image_size
        displacement = math.hypot((cur_center[0] - ref_center[0]) * width,
                                  (cur_center[1] - ref_center[1]) * height)
        diagonal = math.hypot(width, height)
        return displacement / diagonal <= float(self.rules['center_tolerance'])
