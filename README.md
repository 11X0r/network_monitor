---

# Network Monitoring Tool

This Python-based tool continuously monitors network performance by measuring latency, jitter, and stability. It adapts test frequency and parameters based on real-time network conditions, ensuring efficient performance analysis.

---

## Features

- **Dynamic Adjustments:** Test intervals, packet count, and sampling frequency adapt based on network quality and stability.
- **Comprehensive Scoring:** Calculates quality and stability scores for latency and jitter.
- **Logging and Alerts:** Provides real-time logs and alerts for critical network failures.
- **Customizable Settings:** Configurable thresholds and test parameters.

---

## Installation

### Prerequisites

1. **Python 3.10 or later**  
   Ensure Python is installed. Verify with:
   ```bash
   python --version
   ```

2. **`ping_stats` Binary**  
   The tool requires the `ping_stats` utility in your PATH. Confirm its availability:
   ```bash
   which ping_stats
   ```

### Install Dependencies

1. Clone the repository:
   ```bash
   git clone <repository_url>
   cd <repository_name>
   ```

2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

---

## Usage

### Start Monitoring

Run the tool using the following command:
```bash
sudo env "PATH=$PATH" python3 monitor.py
```

**Explanation:**
- `sudo` is necessary because the `ping_stats` utility requires superuser privileges to send ICMP packets and access network interfaces.
- `env "PATH=$PATH"` ensures the script has access to the current user's PATH environment, including the location of `ping_stats` and dependencies, as `sudo` by default resets the PATH.

### Logs and Alerts

- Logs are saved to `network_monitor.log` in the current directory.
- Real-time output is displayed in the terminal.

### Stop Monitoring

Terminate the program gracefully with `Ctrl+C`.

---

## Testing

The project includes unit tests to validate core functionalities. Run the tests using:
```bash
pytest -v test_monitor.py
```

Ensure all dependencies for testing (`pytest`, `pytest-mock`) are installed.

---

## Troubleshooting

1. **`ping_stats not found in PATH`:**  
   Ensure `ping_stats` is installed and available in your system PATH.

2. **Permission Errors with `sudo`:**  
   The tool requires `sudo` privileges for certain commands. Update your `sudoers` file if necessary.

3. **Network Performance Issues:**  
   Adjust thresholds and sampling parameters in `settings.toml` to better suit your environment.

---