import logging
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import tomli


def load_config() -> dict:
    config_path = Path(__file__).parent / "config" / "settings.toml"
    with open(config_path, "rb") as f:
        return tomli.load(f)


CONFIG = load_config()


def setup_logging() -> None:
    """Configure logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('network_monitor.log')
        ]
    )


@dataclass
class NetworkMetrics:
    timestamp: datetime
    latency: float
    jitter: float
    success: bool = True
    quality: Optional[float] = None
    packets: int = 0
    packet_interval: float = 0  # ms between packets

    def calculate_quality(self) -> float:
        """Calculate quality score [0 (best) - 100 (worst)]"""
        if not self.success:
            return 100.0

        norm_latency = min(self.latency / CONFIG["scoring"]["limits"]["max_latency"], 1.0)
        norm_jitter = min(self.jitter / CONFIG["scoring"]["limits"]["max_jitter"], 1.0)

        return (norm_latency * CONFIG["scoring"]["weights"]["latency"] +
                norm_jitter * CONFIG["scoring"]["weights"]["jitter"]) * 100

    def get_quality_level(self) -> str:
        """Human-readable quality level"""
        if self.quality is None:
            self.quality = self.calculate_quality()

        if self.quality <= CONFIG["quality_scale"]["excellent"]:
            return "Excellent"
        elif self.quality <= CONFIG["quality_scale"]["good"]:
            return "Good"
        elif self.quality <= CONFIG["quality_scale"]["fair"]:
            return "Fair"
        elif self.quality <= CONFIG["quality_scale"]["poor"]:
            return "Poor"
        return "Very Poor"

    def __str__(self) -> str:
        if not self.success:
            return "[Connection unavailable]"

        if self.quality is None:
            self.quality = self.calculate_quality()

        return (f"[P:{self.packets} PI:{self.packet_interval:.1f}ms] "
                f"L:{self.latency:.1f}ms J:{self.jitter:.1f}ms "
                f"Q:{self.quality:.1f} ({self.get_quality_level()})")


class TestContext:
    """Handles individual test execution and calibration"""

    def __init__(self, config: dict):
        self.config = config
        self.ping_stats_path = self._find_ping_stats()
        self.packets = config["network"]["packets"]["default"]
        self.packet_interval = config["network"]["interval"]["default"]

    def execute_test(self) -> NetworkMetrics:
        try:
            result = self._execute_ping()

            if result.returncode == 0:
                metrics = self._parse_ping_result(result.stdout)
                if metrics:
                    # Early failure check - if metrics are extremely poor, mark as failed
                    if (metrics.latency > self.config["thresholds"]["latency"]["critical"] or
                            metrics.jitter > self.config["thresholds"]["jitter"]["critical"]):
                        return NetworkMetrics(
                            timestamp=datetime.now(),
                            latency=0,
                            jitter=0,
                            success=False,
                            packets=self.packets,
                            packet_interval=self.packet_interval
                        )

                    metrics.quality = metrics.calculate_quality()
                    return metrics

            return NetworkMetrics(
                timestamp=datetime.now(),
                latency=0,
                jitter=0,
                success=False,
                packets=self.packets,
                packet_interval=self.packet_interval
            )

        except Exception as e:
            logging.error(f"Test execution failed: {e}")
            return NetworkMetrics(
                timestamp=datetime.now(),
                latency=0,
                jitter=0,
                success=False,
                packets=self.packets,
                packet_interval=self.packet_interval
            )

    def adjust_parameters(self, metrics: NetworkMetrics) -> None:
        """Adjust test parameters based on the latest metrics"""
        if not metrics.success:
            return

        # Adjust packets and interval based on quality
        if metrics.quality > self.config["thresholds"]["quality"]["poor"]:
            # Poor quality - increase sampling
            self.packets = min(
                self.packets + self.config["network"]["packets"]["step"],
                self.config["network"]["packets"]["max"]
            )
            # Reduce interval by 20%
            self.packet_interval = max(
                self.packet_interval * 0.8,
                self.config["network"]["interval"]["min"]
            )
        elif metrics.quality < self.config["thresholds"]["quality"]["excellent"]:
            # Excellent quality - decrease sampling
            self.packets = max(
                self.packets - self.config["network"]["packets"]["step"],
                self.config["network"]["packets"]["min"]
            )
            # Increase interval by 20%
            self.packet_interval = min(
                self.packet_interval * 1.2,
                self.config["network"]["interval"]["max"]
            )

    def _find_ping_stats(self) -> str:
        ping_stats_path = shutil.which('ping_stats')
        if not ping_stats_path:
            raise FileNotFoundError("ping_stats not found in PATH")
        return ping_stats_path

    def _execute_ping(self) -> subprocess.CompletedProcess:
        cmd = [
            "sudo", "-n",
            self.ping_stats_path,
            self.config["network"]["target_server"],
            str(int(self.packets)),
            str(float(self.packet_interval))
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def _parse_ping_result(self, output: str) -> Optional[NetworkMetrics]:
        latency_match = re.search(r"Average Latency (\d+\.?\d*)ms", output)
        jitter_match = re.search(r"Jitter (\d+\.?\d*)ms", output)

        if latency_match and jitter_match:
            return NetworkMetrics(
                timestamp=datetime.now(),
                latency=float(latency_match.group(1)),
                jitter=float(jitter_match.group(1)),
                packets=self.packets,
                packet_interval=self.packet_interval
            )
        return None


class WindowContext:
    """Manages the monitoring window and test frequency"""

    def __init__(self, config: dict):
        self.config = config
        self.window_minutes = config["window"]["duration"]
        self.target_tests = config["window"]["target_tests"]
        self.metrics = deque(maxlen=config["window"]["max_tests"])
        self.start_time = datetime.now()
        self.test_count = 0

        # Calculate interval to achieve target_tests in window_minutes
        self.test_interval = (self.window_minutes * 60) / (self.target_tests - 1)
        self.last_test_time = None
        logging.info(
            f"Test interval: {self.test_interval:.1f}s (for {self.target_tests} tests in {self.window_minutes}m window)")

    def add_metric(self, metric: NetworkMetrics) -> None:
        self.metrics.append(metric)
        self.test_count += 1
        self.last_test_time = datetime.now()

    def is_window_complete(self) -> bool:
        elapsed = datetime.now() - self.start_time
        return elapsed >= timedelta(minutes=self.window_minutes)

    def analyse_window(self) -> float:
        """
        Calculate a stability score (0 to 1) using recent metrics.
        A stable network has consistent latency and jitter within acceptable bounds.
        """
        if len(self.metrics) < self.config["thresholds"]["stability"]["min_samples"]:
            return 1.0

        # Use only recent metrics
        recent_metrics = [m for m in list(self.metrics)[-self.config["thresholds"]["stability"]["min_samples"]:] if
                          m.success]
        if not recent_metrics:
            return 0.0

        # Extract latencies and jitters
        latencies = [m.latency for m in recent_metrics]
        jitters = [m.jitter for m in recent_metrics]

        # Compute mean and variance for stability
        avg_latency = sum(latencies) / len(latencies)
        avg_jitter = sum(jitters) / len(jitters)
        var_latency = sum((l - avg_latency) ** 2 for l in latencies) / len(latencies)
        var_jitter = sum((j - avg_jitter) ** 2 for j in jitters) / len(jitters)

        # Normalise and calculate stability as inversely proportional to variance
        norm_latency_var = min(var_latency / self.config["thresholds"]["stability"]["latency_variance"], 1.0)
        norm_jitter_var = min(var_jitter / self.config["thresholds"]["stability"]["jitter_variance"], 1.0)

        stability = 1.0 - ((norm_latency_var + norm_jitter_var) / 2)

        # Ensure score is between 0 and 1
        return max(0.0, stability)

    def adjust_frequency(self, stability: float) -> None:
        """Adjust test frequency based on window analysis"""
        original_target = self.target_tests

        if stability < self.config["window"]["adaptation"]["stability_threshold"]:
            # Network unstable - increase test frequency
            self.target_tests = min(
                int(self.target_tests * 1.2),  # 20% increase
                self.config["window"]["max_tests"]
            )
        elif stability > 0.9:
            # Network stable - decrease test frequency
            self.target_tests = max(
                int(self.target_tests * 0.8),  # 20% decrease
                self.config["window"]["min_tests"]
            )

        if self.target_tests != original_target:
            # Recalculate exact interval for new target (-1 to account for first test)
            self.test_interval = (self.window_minutes * 60) / (self.target_tests - 1)
            logging.info(
                f"Adjusted test frequency - "
                f"Tests per window: {original_target}->{self.target_tests}, "
                f"New interval: {self.test_interval:.1f}s "
                f"(Stability: {stability:.2f})"
            )

    def reset_window(self) -> None:
        """Reset window state"""
        logging.info(
            f"Window complete - "
            f"Tests completed: {self.test_count}/{self.target_tests} "
            f"(Target: {self.target_tests} tests per {self.window_minutes}m window)"
        )
        self.start_time = datetime.now()
        self.test_count = 0
        self.last_test_time = None
        self.metrics.clear()


class NetworkMonitor:
    """Main monitoring class that coordinates TestContext and WindowContext"""

    def __init__(self):
        self.config = load_config()
        self.test_context = TestContext(self.config)
        self.window_context = WindowContext(self.config)
        self.backoff_time = 1
        self.failure_count = 0

    def wait_for_next_test(self) -> None:
        """Enforce test interval"""
        if not self.window_context.last_test_time:
            return

        elapsed = (datetime.now() - self.window_context.last_test_time).total_seconds()
        if elapsed < self.window_context.test_interval:
            time.sleep(self.window_context.test_interval - elapsed)

    def run(self) -> None:
        logging.info(
            f"Starting network monitoring "
            f"(Window: {self.window_context.window_minutes}m, "
            f"Initial target: {self.window_context.target_tests} tests/window, "
            f"Interval: {self.window_context.test_interval:.1f}s)"
        )

        try:
            while True:
                # Enforce test interval
                self.wait_for_next_test()

                metrics = self.test_context.execute_test()

                if not metrics.success:
                    self.failure_count += 1
                    if self.failure_count >= 3:
                        print(f"\n{datetime.now().strftime('%H:%M:%S.%f')[:-3]} - [Connection unavailable]")
                        time.sleep(min(30, self.backoff_time))
                        self.backoff_time *= 2
                    continue

                self.failure_count = 0
                self.backoff_time = 1
                self.window_context.add_metric(metrics)
                print(f"\n{datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {metrics}")

                self.test_context.adjust_parameters(metrics)

                if self.window_context.is_window_complete():
                    stability = self.window_context.analyse_window()
                    self.window_context.adjust_frequency(stability)
                    self.window_context.reset_window()

        except KeyboardInterrupt:
            logging.info("Monitoring stopped by user")
            sys.exit(0)


def main():
    setup_logging()
    try:
        monitor = NetworkMonitor()
        monitor.run()
    except FileNotFoundError as e:
        logging.error(f"Startup error: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
