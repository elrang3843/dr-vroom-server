"""
Dr. Vroom Brain — Signal Processing & AI Diagnosis Engine
닥터브릉이의 두뇌: 소리를 듣고, 분석하고, 기억하고, 성장한다
"""
import numpy as np
from scipy import signal as scipy_signal
from typing import List, Dict, Optional, Tuple
import json
import math


class DrVroomBrain:
    """
    닥터브릉이의 핵심 두뇌 클래스
    - FFT 기반 주파수 분석 (특허 US 12,349,291 B2)
    - 지식 기반 학습 시스템
    - 신뢰도 기반 진단
    """

    SAMPLE_RATE = 44100
    FFT_SIZE = 4096

    # 부품별 주파수 대역
    FREQ_BANDS = {
        "engine":       (20.0,   200.0),
        "transmission": (200.0,  1000.0),
        "bearing":      (1000.0, 5000.0),
        "brake":        (50.0,   500.0),
        "exhaust":      (20.0,   150.0),
        "belt":         (500.0,  3000.0),
    }

    # 고장 패턴 시그니처 (초기 지식 — 경험으로 업데이트됨)
    FAULT_SIGNATURES = {
        "engine_misfire":    {"freq_range": (25, 80),   "energy_threshold": 0.4},
        "engine_knock":      {"freq_range": (60, 160),  "energy_threshold": 0.55},
        "bearing_outer":     {"freq_range": (1200, 2500), "energy_threshold": 0.45},
        "bearing_inner":     {"freq_range": (2500, 4500), "energy_threshold": 0.45},
        "transmission_gear": {"freq_range": (300, 800),  "energy_threshold": 0.40},
        "brake_rotor":       {"freq_range": (60, 200),   "energy_threshold": 0.42},
        "exhaust_leak":      {"freq_range": (30, 120),   "energy_threshold": 0.38},
        "belt_wear":         {"freq_range": (600, 2000),  "energy_threshold": 0.38},
    }

    @staticmethod
    def compute_fft(samples: List[float]) -> Tuple[np.ndarray, np.ndarray]:
        """FFT 계산 — 핵심 주파수 분석"""
        n = len(samples)
        if n == 0:
            return np.array([]), np.array([])

        arr = np.array(samples, dtype=np.float64)

        # Hanning window (spectral leakage 방지)
        window = np.hanning(n)
        windowed = arr * window

        # FFT
        fft_result = np.fft.rfft(windowed)
        magnitudes = np.abs(fft_result) / n
        freqs = np.fft.rfftfreq(n, d=1.0 / DrVroomBrain.SAMPLE_RATE)

        return freqs, magnitudes

    @staticmethod
    def extract_features(samples: List[float]) -> Dict:
        """
        오디오 특징 추출 — 닥터브릉이가 소리를 '기억'하는 방법
        """
        if not samples:
            return {}

        arr = np.array(samples, dtype=np.float64)
        freqs, mags = DrVroomBrain.compute_fft(samples)

        features = {}

        # 시간 도메인 특징
        features["rms"] = float(np.sqrt(np.mean(arr ** 2)))
        features["peak"] = float(np.max(np.abs(arr)))
        features["crest_factor"] = features["peak"] / (features["rms"] + 1e-10)
        features["zero_crossing_rate"] = float(
            np.sum(np.diff(np.sign(arr)) != 0) / len(arr)
        )

        # 주파수 도메인 특징
        if len(freqs) > 0 and len(mags) > 0:
            # 지배 주파수
            dominant_idx = np.argmax(mags)
            features["dominant_freq"] = float(freqs[dominant_idx])
            features["dominant_mag"] = float(mags[dominant_idx])

            # 스펙트럴 센트로이드
            features["spectral_centroid"] = float(
                np.sum(freqs * mags) / (np.sum(mags) + 1e-10)
            )

            # 각 부품 주파수 대역별 에너지
            band_energy = {}
            for comp, (fmin, fmax) in DrVroomBrain.FREQ_BANDS.items():
                mask = (freqs >= fmin) & (freqs <= fmax)
                if np.any(mask):
                    band_energy[comp] = float(np.sqrt(np.mean(mags[mask] ** 2)))
                else:
                    band_energy[comp] = 0.0
            features["band_energy"] = band_energy

            # Top 5 주파수 피크 (지식 서명으로 활용)
            peak_indices = np.argsort(mags)[-5:][::-1]
            features["freq_peaks"] = [
                {"freq": float(freqs[i]), "mag": float(mags[i])}
                for i in peak_indices if i < len(freqs)
            ]

        return features

    @staticmethod
    def diagnose_component(
        component: str,
        features: Dict,
        knowledge_items: List[Dict] = None,
    ) -> Dict:
        """
        단일 부품 진단
        - 알고 있는 지식이 있으면 지식 기반으로 판단
        - 없으면 기본 알고리즘으로 판단
        - 항상 신뢰도와 함께 반환
        """
        band_energy = features.get("band_energy", {})
        energy = band_energy.get(component, 0.0)
        overall_rms = features.get("rms", 0.0)
        dominant_freq = features.get("dominant_freq", 0.0)

        normalized = energy / (overall_rms + 1e-10)

        # ── 지식 기반 판단 (경험에서 배운 것) ──
        if knowledge_items:
            return DrVroomBrain._knowledge_based_diagnosis(
                component, features, knowledge_items, normalized
            )

        # ── 기본 알고리즘 판단 (태어났을 때 가진 본능) ──
        return DrVroomBrain._baseline_diagnosis(
            component, energy, normalized, dominant_freq, overall_rms
        )

    @staticmethod
    def _knowledge_based_diagnosis(
        component: str,
        features: Dict,
        knowledge_items: List[Dict],
        normalized_energy: float,
    ) -> Dict:
        """지식 DB에서 가장 유사한 패턴 찾아 진단"""
        dominant_freq = features.get("dominant_freq", 0.0)
        rms = features.get("rms", 0.0)

        best_match = None
        best_similarity = 0.0

        for item in knowledge_items:
            # 주파수 유사도 계산
            freq_diff = abs(item.get("dominant_freq", 0) - dominant_freq)
            freq_sim = 1.0 / (1.0 + freq_diff / 100.0)

            # 에너지 유사도
            energy_diff = abs(item.get("rms_amplitude", 0) - rms)
            energy_sim = 1.0 / (1.0 + energy_diff * 10.0)

            # 가중 유사도
            similarity = (freq_sim * 0.6 + energy_sim * 0.4) * item.get("confidence", 0.5)

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = item

        if best_match and best_similarity > 0.4:
            confidence = min(0.98, best_similarity * best_match.get("confidence", 0.5) * 1.5)
            return {
                "status": best_match.get("status", "unknown"),
                "fault_code": best_match.get("fault_code", ""),
                "description": best_match.get("description", ""),
                "confidence": confidence,
                "dominant_freq": dominant_freq,
                "energy": normalized_energy,
                "knowledge_based": True,
                "matched_knowledge_id": best_match.get("id", ""),
            }

        # 매칭 안 되면 기본 알고리즘으로 fallback
        return DrVroomBrain._baseline_diagnosis(
            component, features.get("band_energy", {}).get(component, 0.0),
            normalized_energy, dominant_freq, features.get("rms", 0.0)
        )

    @staticmethod
    def _baseline_diagnosis(
        component: str,
        energy: float,
        normalized: float,
        dominant_freq: float,
        overall_rms: float,
    ) -> Dict:
        """기본 임계값 기반 진단 (초기 본능)"""
        thresholds = {
            "engine":       (0.40, 0.70),
            "transmission": (0.35, 0.65),
            "bearing":      (0.30, 0.55),
            "brake":        (0.35, 0.60),
            "exhaust":      (0.45, 0.70),
            "belt":         (0.32, 0.58),
        }

        fault_map = {
            "engine":       ("engine_rough", "engine_knock"),
            "transmission": ("transmission_wear", "transmission_gear"),
            "bearing":      ("bearing_wear", "bearing_defect"),
            "brake":        ("brake_squeal", "brake_rotor"),
            "exhaust":      ("exhaust_loose", "exhaust_leak"),
            "belt":         ("belt_tension", "belt_wear"),
        }

        warn_t, crit_t = thresholds.get(component, (0.40, 0.65))
        warn_f, crit_f = fault_map.get(component, ("unknown_warn", "unknown_crit"))

        if normalized > crit_t or energy > 0.60:
            return {
                "status": "critical",
                "fault_code": crit_f,
                "description": f"Critical vibration at {dominant_freq:.0f}Hz. Immediate inspection required.",
                "confidence": min(0.85, normalized),
                "dominant_freq": dominant_freq,
                "energy": normalized,
                "knowledge_based": False,
            }
        elif normalized > warn_t or energy > 0.35:
            return {
                "status": "warning",
                "fault_code": warn_f,
                "description": f"Abnormal vibration pattern at {dominant_freq:.0f}Hz. Monitor closely.",
                "confidence": 0.65,
                "dominant_freq": dominant_freq,
                "energy": normalized,
                "knowledge_based": False,
            }
        else:
            return {
                "status": "normal",
                "fault_code": "",
                "description": f"Normal vibration characteristics. No fault detected.",
                "confidence": 0.88,
                "dominant_freq": dominant_freq,
                "energy": normalized,
                "knowledge_based": False,
            }

    @staticmethod
    def calculate_health_score(component_results: List[Dict]) -> float:
        """전체 차량 건강 점수 (0-100)"""
        if not component_results:
            return 0.0
        score_map = {"normal": 100.0, "warning": 55.0, "critical": 10.0, "unknown": 50.0}
        total = sum(score_map.get(r.get("status", "unknown"), 50.0) for r in component_results)
        return total / len(component_results)

    @staticmethod
    def compute_feature_similarity(features_a: Dict, features_b: Dict) -> float:
        """두 오디오 특징 벡터의 유사도 계산 (0~1)"""
        score = 0.0
        count = 0

        # 지배 주파수 유사도
        fa = features_a.get("dominant_freq", 0)
        fb = features_b.get("dominant_freq", 0)
        if fa > 0 and fb > 0:
            score += 1.0 / (1.0 + abs(fa - fb) / 200.0)
            count += 1

        # RMS 유사도
        ra = features_a.get("rms", 0)
        rb = features_b.get("rms", 0)
        if ra > 0 and rb > 0:
            score += 1.0 / (1.0 + abs(ra - rb) * 20.0)
            count += 1

        # 대역 에너지 유사도
        ba = features_a.get("band_energy", {})
        bb = features_b.get("band_energy", {})
        for comp in DrVroomBrain.FREQ_BANDS:
            ea = ba.get(comp, 0.0)
            eb = bb.get(comp, 0.0)
            if ea > 0 or eb > 0:
                score += 1.0 / (1.0 + abs(ea - eb) * 10.0)
                count += 1

        return score / count if count > 0 else 0.0

    @staticmethod
    def generate_demo_signal(
        inject_fault: bool = False,
        fault_type: str = "bearing",
        duration_ms: int = 5000,
    ) -> List[float]:
        """데모 신호 생성 (테스트/교육용)"""
        n_samples = int(DrVroomBrain.SAMPLE_RATE * duration_ms / 1000)
        t = np.linspace(0, duration_ms / 1000, n_samples)

        # 기본 엔진 진동 (30Hz idle)
        signal = (
            0.12 * np.sin(2 * np.pi * 30 * t) +
            0.06 * np.sin(2 * np.pi * 60 * t) +
            0.03 * np.sin(2 * np.pi * 90 * t) +
            0.02 * np.random.randn(n_samples)
        )

        if inject_fault:
            fault_signals = {
                "bearing": lambda t: 0.35 * np.sin(2 * np.pi * 2200 * t) * np.exp(-50 * (t % 0.01)),
                "engine":  lambda t: 0.45 * np.sin(2 * np.pi * 85 * t) * (1 + 0.5 * np.sin(2 * np.pi * 5 * t)),
                "transmission": lambda t: 0.30 * np.sin(2 * np.pi * 600 * t) + 0.15 * np.sin(2 * np.pi * 1200 * t),
                "brake":   lambda t: 0.38 * np.sin(2 * np.pi * 180 * t) * (1 + 0.3 * np.random.randn(len(t))),
            }
            if fault_type in fault_signals:
                signal += fault_signals[fault_type](t)

        # Normalize
        max_val = np.max(np.abs(signal))
        if max_val > 0:
            signal = signal / max_val

        return signal.tolist()
