"""Noise-reduction processing via noisereduce."""

from __future__ import annotations

import logging

import numpy as np

logger: logging.Logger = logging.getLogger(__name__)

# ``noisereduce`` pulls in scipy at import time, which is slow enough to
# delay the GUI window appearing.  Import it on first use instead of at
# module import time (see :meth:`AudioFilter.reduce_noise`).


class AudioFilter:
    """Wraps spectral-gate noise reduction for cleaning raw captures.

    Parameters
    ----------
    sample_rate : int
        Sample rate used during recording (needed by ``noisereduce``).
    prop_decrease : float
        Strength of noise reduction in [0.0, 1.0].  Higher = more
        aggressive.  Default ``0.75``.
    n_std_thresh_stationary : float
        Threshold for stationary-noise estimation.  Default ``1.5``.
    """

    def __init__(
        self,
        sample_rate: int = 44_100,
        prop_decrease: float = 0.75,
        n_std_thresh_stationary: float = 1.5,
    ) -> None:
        self.sample_rate: int = sample_rate
        self.prop_decrease: float = prop_decrease
        self.n_std_thresh_stationary: float = n_std_thresh_stationary
        logger.info(
            "AudioFilter initialised  prop_decrease=%.2f", self.prop_decrease
        )

    # ------------------------------------------------------------------
    def reduce_noise(self, audio: np.ndarray) -> np.ndarray:
        """Apply spectral-gate noise reduction.

        Parameters
        ----------
        audio : np.ndarray
            Input float32 samples.

        Returns
        -------
        np.ndarray
            Cleaned audio array (same shape and dtype).
        """
        logger.info(
            "Applying noise reduction  prop_decrease=%.2f", self.prop_decrease
        )
        import noisereduce as nr

        try:
            reduced: np.ndarray = nr.reduce_noise(
                y=audio,
                sr=self.sample_rate,
                prop_decrease=self.prop_decrease,
                n_std_thresh_stationary=self.n_std_thresh_stationary,
            )
        except Exception as exc:
            logger.error("Noise reduction failed – returning original: %s", exc)
            return audio

        logger.info("Noise reduction complete.")
        return reduced.astype(np.float32)
