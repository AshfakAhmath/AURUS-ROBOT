"""Persistent face identity enrollment and recognition."""

from __future__ import annotations

import threading

import numpy as np

from src.core.models import EnrollmentStatus, IdentityResult


def _normalize(vector: np.ndarray) -> np.ndarray:
    data = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(data))
    if norm <= 1e-8:
        raise ValueError("empty face embedding")
    return data / norm


class IdentityService:
    def __init__(
        self,
        repository,
        threshold: float = 0.40,
        uncertain_threshold: float = 0.30,
        required_samples: int = 20,
        confirmation_frames: int = 3,
    ):
        self.repository = repository
        self.threshold = threshold
        self.uncertain_threshold = min(uncertain_threshold, threshold)
        self.required_samples = required_samples
        self.confirmation_frames = confirmation_frames
        self._lock = threading.RLock()
        self._known: dict[int, tuple[str, np.ndarray]] = {}
        self._samples: list[np.ndarray] = []
        self._enroll_name: str | None = None
        self._enrollment = EnrollmentStatus(required=required_samples)
        self._candidate_id: int | None = None
        self._candidate_frames = 0
        self._current = IdentityResult()
        self.reload()

    def reload(self) -> None:
        known = {}
        for user_id, name, vector in self.repository.load_embeddings():
            try:
                known[user_id] = (name, _normalize(vector))
            except ValueError:
                continue
        with self._lock:
            self._known = known

    def begin_enrollment(self, name: str) -> EnrollmentStatus:
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("name is required")
        with self._lock:
            self._samples = []
            self._enroll_name = clean
            self._enrollment = EnrollmentStatus(active=True, name=clean, required=self.required_samples)
            return self._enrollment

    def begin_session_identity(self, name: str) -> EnrollmentStatus:
        """Fallback when SFace is unavailable: persist name/facts, not biometric matching."""
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("name is required")
        user_id = self.repository.ensure_user(clean)
        with self._lock:
            self._current = IdentityResult("known", user_id, clean, 1.0)
            self._enrollment = EnrollmentStatus(
                active=False,
                name=clean,
                accepted=1,
                required=1,
                complete=True,
                error="session identity only; SFace model unavailable",
            )
            return self._enrollment

    def cancel_enrollment(self, error: str | None = None) -> None:
        with self._lock:
            self._samples = []
            self._enroll_name = None
            self._enrollment = EnrollmentStatus(required=self.required_samples, error=error)

    def get_enrollment(self) -> EnrollmentStatus:
        with self._lock:
            return self._enrollment

    def current(self) -> IdentityResult:
        with self._lock:
            return self._current

    def clear_current(self) -> None:
        with self._lock:
            self._candidate_id = None
            self._candidate_frames = 0
            self._current = IdentityResult()

    def process_embedding(self, embedding: np.ndarray | None) -> IdentityResult:
        if embedding is None:
            self.clear_current()
            return self.current()

        try:
            vector = _normalize(embedding)
        except ValueError:
            self.clear_current()
            return self.current()

        with self._lock:
            if self._enroll_name:
                self._samples.append(vector)
                accepted = len(self._samples)
                self._enrollment = EnrollmentStatus(
                    active=accepted < self.required_samples,
                    name=self._enroll_name,
                    accepted=accepted,
                    required=self.required_samples,
                    complete=accepted >= self.required_samples,
                )
                if accepted >= self.required_samples:
                    name = self._enroll_name
                    mean = _normalize(np.mean(np.stack(self._samples), axis=0))
                    user_id = self.repository.ensure_user(name)
                    self.repository.save_embedding(user_id, mean, accepted)
                    self._known[user_id] = (name, mean)
                    self._samples = []
                    self._enroll_name = None
                    self._current = IdentityResult("known", user_id, name, 1.0)
                    return self._current
                self._current = IdentityResult("uncertain", None, None, accepted / self.required_samples)
                return self._current

            if not self._known:
                self._current = IdentityResult()
                return self._current

            scores = {
                user_id: float(np.dot(vector, known_vector))
                for user_id, (_, known_vector) in self._known.items()
                if known_vector.shape == vector.shape
            }
            if not scores:
                self._current = IdentityResult()
                return self._current

            best_id = max(scores, key=scores.get)
            score = scores[best_id]
            name = self._known[best_id][0]

            if score >= self.threshold:
                if best_id == self._candidate_id:
                    self._candidate_frames += 1
                else:
                    self._candidate_id = best_id
                    self._candidate_frames = 1
                if self._candidate_frames >= self.confirmation_frames:
                    self._current = IdentityResult("known", best_id, name, score)
                else:
                    self._current = IdentityResult("uncertain", None, None, score)
            elif score >= self.uncertain_threshold:
                self._candidate_id = None
                self._candidate_frames = 0
                self._current = IdentityResult("uncertain", None, None, score)
            else:
                self._candidate_id = None
                self._candidate_frames = 0
                self._current = IdentityResult("unknown", None, None, score)
            return self._current
