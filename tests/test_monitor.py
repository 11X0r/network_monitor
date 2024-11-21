from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from monitor import NetworkMonitor, NetworkMetrics, WindowContext, CONFIG
from monitor import TestContext


@dataclass(frozen=True)
class MonitorState:
    """Captures monitoring system state for test verification"""
    packets: int
    packet_interval: float  # ms between packets
    test_interval: float  # seconds between tests
    target_tests: int
    latency: float
    jitter: float
    stability: float


class TestNetworkMonitor:
    @pytest.fixture
    def monitor(self):
        """Create a monitor instance with mocked ping execution"""
        monitor = NetworkMonitor()
        monitor.test_context._execute_ping = Mock()
        monitor.test_context._execute_ping.return_value = Mock(
            returncode=0,
            stdout="Average Latency 20.0ms\nJitter 2.0ms"
        )
        return monitor

    def capture_state(self, monitor: NetworkMonitor) -> MonitorState:
        """Capture current state of the monitor for verification"""
        metrics = (monitor.window_context.metrics[-1]
                   if monitor.window_context.metrics else None)
        stability = monitor.window_context.analyse_window()
        return MonitorState(
            packets=monitor.test_context.packets,
            packet_interval=monitor.test_context.packet_interval,
            test_interval=monitor.window_context.test_interval,
            target_tests=monitor.window_context.target_tests,
            latency=metrics.latency if metrics else 0,
            jitter=metrics.jitter if metrics else 0,
            stability=stability
        )

    def test_monitor_initialization(self, monitor):
        """Test initial monitor state"""
        assert monitor.backoff_time == 1
        assert monitor.failure_count == 0
        assert isinstance(monitor.test_context, TestContext)
        assert isinstance(monitor.window_context, WindowContext)

    def test_test_context_initialization(self):
        """Test TestContext initialization and defaults"""
        config = CONFIG.copy()
        context = TestContext(config)

        assert context.packets == config["network"]["packets"]["default"]
        assert context.packet_interval == config["network"]["interval"]["default"]
        assert context._find_ping_stats() is not None

    def test_window_context_initialization(self):
        """Test WindowContext initialization and calculations"""
        config = CONFIG.copy()
        context = WindowContext(config)

        assert context.window_minutes == config["window"]["duration"]
        assert context.target_tests == config["window"]["target_tests"]
        assert len(context.metrics) == 0
        assert context.test_count == 0

        # Verify test interval calculation
        expected_interval = (context.window_minutes * 60) / (context.target_tests - 1)
        assert abs(context.test_interval - expected_interval) < 0.01

    @pytest.mark.parametrize("sequence", [
        {
            'name': 'perfect_stability',
            'metrics': [(10.0, 1.0)] * 5,
            'expect_stability': 1.0,
            'tolerance': 0.01,
            'desc': "Identical metrics should show perfect stability"
        },
        {
            'name': 'excellent_stability',
            'metrics': [
                (10.0, 1.0), (10.2, 1.1), (9.8, 0.9),
                (10.1, 1.0), (10.0, 1.0)
            ],
            'expect_stability': 0.98,
            'tolerance': 0.05,
            'desc': "Small variations (<5% from mean) should show excellent stability"
        },
        {
            'name': 'good_stability',
            'metrics': [
                (10.0, 1.0), (11.0, 1.2), (9.0, 0.8),
                (10.5, 1.1), (9.5, 0.9)
            ],
            'expect_stability': 0.95,
            'tolerance': 0.10,
            'desc': "Moderate variations (<15% from mean) should show good stability"
        },
        {
            'name': 'moderate_stability',
            'metrics': [
                (10.0, 1.0), (13.0, 1.5), (8.0, 0.7),
                (12.0, 1.4), (9.0, 0.8)
            ],
            'expect_stability': 0.85,
            'tolerance': 0.15,
            'desc': "Larger variations (<30% from mean) should show moderate stability"
        },
        {
            'name': 'poor_stability',
            'metrics': [
                (10.0, 1.0), (18.0, 2.0), (6.0, 0.5),
                (15.0, 1.8), (8.0, 0.7)
            ],
            'expect_stability': 0.45,
            'tolerance': 0.15,
            'desc': "Large variations (>50% from mean) should show poor stability"
        },
        {
            'name': 'latency_spike',
            'metrics': [
                (10.0, 1.0), (40.0, 1.1), (10.2, 1.0),
                (10.1, 0.9), (10.0, 1.0)
            ],
            'expect_stability': 0.50,
            'tolerance': 0.15,
            'desc': "Single latency spike should significantly impact stability"
        },
        {
            'name': 'jitter_spike',
            'metrics': [
                (10.0, 1.0), (10.2, 4.0), (10.1, 1.1),
                (9.9, 1.0), (10.0, 1.0)
            ],
            'expect_stability': 0.85,
            'tolerance': 0.15,
            'desc': "Single jitter spike should moderately impact stability"
        }
    ])
    def test_stability_calculation(self, monitor, sequence):
        """Test stability calculation for different network patterns"""
        print(f"\nTesting {sequence['name']} stability pattern:")
        print("Metrics sequence:", sequence['metrics'])

        monitor.window_context.metrics.clear()

        for latency, jitter in sequence['metrics']:
            metrics = NetworkMetrics(
                timestamp=datetime.now(),
                latency=latency,
                jitter=jitter,
                packets=monitor.test_context.packets,
                packet_interval=monitor.test_context.packet_interval
            )
            monitor.window_context.add_metric(metrics)

        stability = monitor.window_context.analyse_window()
        print(f"Calculated stability: {stability:.4f}")

        tolerance = sequence.get('tolerance', 0.05)
        expected = sequence['expect_stability']
        assert abs(stability - expected) <= tolerance, (
            f"{sequence['desc']} - "
            f"Expected stability {expected:.2f}±{tolerance:.2f}, "
            f"got {stability:.4f}"
        )

    def test_edge_cases(self, monitor):
        """Test edge cases in stability calculation"""
        cases = [
            {
                'name': "empty_metrics",
                'metrics': [],
                'expect': 1.0
            },
            {
                'name': "single_metric",
                'metrics': [(10.0, 1.0)],
                'expect': 1.0
            },
            {
                'name': "all_failed",
                'metrics': [NetworkMetrics(
                    timestamp=datetime.now(),
                    latency=0,
                    jitter=0,
                    success=False,
                    packets=10,
                    packet_interval=1.0
                ) for _ in range(5)],
                'expect': 0.0
            }
        ]

        for case in cases:
            print(f"\nTesting edge case: {case['name']}")
            monitor.window_context.metrics.clear()

            for m in case['metrics']:
                if isinstance(m, tuple):
                    latency, jitter = m
                    metrics = NetworkMetrics(
                        timestamp=datetime.now(),
                        latency=latency,
                        jitter=jitter,
                        packets=monitor.test_context.packets,
                        packet_interval=monitor.test_context.packet_interval
                    )
                    monitor.window_context.add_metric(metrics)
                else:
                    monitor.window_context.add_metric(m)

            stability = monitor.window_context.analyse_window()
            print(f"Calculated stability: {stability:.4f}")

            assert stability == case['expect'], \
                f"Expected {case['expect']}, got {stability}"

    def test_network_metrics(self):
        """Test NetworkMetrics functionality"""
        metrics = NetworkMetrics(
            timestamp=datetime.now(),
            latency=20.0,
            jitter=2.0,
            packets=10,
            packet_interval=1.0
        )

        # Test quality calculation
        quality = metrics.calculate_quality()
        assert 0 <= quality <= 100, "Quality should be between 0 and 100"

        # Test quality levels
        metrics.quality = 5.0
        assert metrics.get_quality_level() == "Excellent"
        metrics.quality = 15.0
        assert metrics.get_quality_level() == "Good"
        metrics.quality = 30.0
        assert metrics.get_quality_level() == "Fair"
        metrics.quality = 50.0
        assert metrics.get_quality_level() == "Poor"
        metrics.quality = 80.0
        assert metrics.get_quality_level() == "Very Poor"

        # Test string representation
        str_repr = str(metrics)
        assert "ms" in str_repr
        assert "Quality" not in str_repr  # Should use Q: abbreviation

    def test_parameter_adjustments(self, monitor):
        """Test parameter adjustment logic"""
        test_cases = [
            {
                'name': "excellent_quality",
                'quality': 5.0,
                'expect_packets': 'decrease',
                'expect_interval': 'increase'
            },
            {
                'name': "poor_quality",
                'quality': 70.0,
                'expect_packets': 'increase',
                'expect_interval': 'decrease'
            }
        ]

        for case in test_cases:
            initial_packets = monitor.test_context.packets
            initial_interval = monitor.test_context.packet_interval

            metrics = NetworkMetrics(
                timestamp=datetime.now(),
                latency=20.0,
                jitter=2.0,
                packets=initial_packets,
                packet_interval=initial_interval,
                quality=case['quality']
            )

            monitor.test_context.adjust_parameters(metrics)

            if case['expect_packets'] == 'decrease':
                assert monitor.test_context.packets < initial_packets
            elif case['expect_packets'] == 'increase':
                assert monitor.test_context.packets > initial_packets

            if case['expect_interval'] == 'decrease':
                assert monitor.test_context.packet_interval < initial_interval
            elif case['expect_interval'] == 'increase':
                assert monitor.test_context.packet_interval > initial_interval

    def test_window_management(self, monitor):
        """Test window timing and completion"""
        window_duration = 0.5  # minutes
        monitor.window_context.window_minutes = window_duration

        # Test window not complete
        monitor.window_context.start_time = datetime.now()
        assert not monitor.window_context.is_window_complete()

        # Test window complete
        monitor.window_context.start_time = datetime.now() - timedelta(minutes=window_duration + 0.1)
        assert monitor.window_context.is_window_complete()

        # Test window reset
        monitor.window_context.reset_window()
        assert monitor.window_context.test_count == 0
        assert len(monitor.window_context.metrics) == 0
        assert (datetime.now() - monitor.window_context.start_time).total_seconds() < 1

    def test_critical_thresholds(self, monitor):
        """Test handling of critical threshold violations"""
        # Test latency threshold
        monitor.test_context._execute_ping.return_value.stdout = (
            f"Average Latency {CONFIG['thresholds']['latency']['critical'] + 1}ms\n"
            f"Jitter 2.0ms"
        )
        metrics = monitor.test_context.execute_test()
        assert not metrics.success

        # Test jitter threshold
        monitor.test_context._execute_ping.return_value.stdout = (
            f"Average Latency 20.0ms\n"
            f"Jitter {CONFIG['thresholds']['jitter']['critical'] + 1}ms"
        )
        metrics = monitor.test_context.execute_test()
        assert not metrics.success

    @patch('time.sleep')
    def test_test_timing(self, mock_sleep, monitor):
        """Test test interval enforcement"""
        # Should not sleep on first test
        monitor.wait_for_next_test()
        mock_sleep.assert_not_called()

        # Should sleep to maintain interval
        monitor.window_context.last_test_time = datetime.now() - timedelta(
            seconds=monitor.window_context.test_interval / 2
        )
        monitor.wait_for_next_test()
        mock_sleep.assert_called_once()

        # Should not sleep if interval has passed
        monitor.window_context.last_test_time = datetime.now() - timedelta(
            seconds=monitor.window_context.test_interval * 2
        )
        monitor.wait_for_next_test()
        # Sleep call count should still be 1
        assert mock_sleep.call_count == 1
