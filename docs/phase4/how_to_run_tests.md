# How to Run Phase 4 Tests

This guide explains how to run the comprehensive Phase 4 test suite for the Nurse Triage Assistant.

---

## Quick Start

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Test Category
```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Load tests only
pytest tests/load/ -v
```

---

## Installation

### 1. Install Test Dependencies
```bash
pip install pytest pytest-asyncio pytest-cov
```

### 2. Verify Installation
```bash
pytest --version
```

---

## Running Test Suites

### Unit Tests
Fast, deterministic tests that don't require live LLM calls.

```bash
# All unit tests
pytest tests/unit/ -v

# Specific test file
pytest tests/unit/test_json_validation.py -v

# Specific test class
pytest tests/unit/test_json_validation.py::TestMissingRequiredFields -v

# Specific test function
pytest tests/unit/test_json_validation.py::TestMissingRequiredFields::test_missing_disposition -v
```

### Integration Tests
Tests with mocked LLM that validate end-to-end behavior.

```bash
# All integration tests
pytest tests/integration/ -v

# SBAR-first behavior tests (NEW - HIGH PRIORITY)
pytest tests/integration/test_sbar_first.py -v

# Adversarial caller tests
pytest tests/integration/test_adversarial.py -v

# Failure mode tests
pytest tests/integration/test_failures.py -v
```

### Load Tests
Concurrency and performance tests.

```bash
# All load tests
pytest tests/load/ -v

# Specific load scenario
pytest tests/load/test_concurrent.py::TestConcurrentSessions::test_10_concurrent_sessions -v
```

### Golden Call Tests
Tests using the synthetic case dataset.

```bash
# Run golden calls through orchestrator
pytest tests/integration/test_sbar_first.py::TestGoldenCallsWithSBARFirst -v
```

---

## Test Execution Modes

### Verbose Output
```bash
pytest tests/ -vv  # Extra verbose
```

### Quiet Mode
```bash
pytest tests/ -q   # Minimal output
```

### Show Print Statements
```bash
pytest tests/ -s   # Capture disabled; see print() output
```

### Stop on First Failure
```bash
pytest tests/ -x   # Exit on first failure
```

### Run Last Failed
```bash
pytest tests/ --lf
```

---

## Coverage Reporting

### Generate Coverage Report
```bash
# Run all tests with coverage
pytest tests/ --cov=src --cov-report=html

# View coverage in browser
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov\index.html  # Windows
```

### Coverage Threshold
```bash
# Fail if coverage below 80%
pytest tests/ --cov=src --cov-fail-under=80
```

### Coverage Report Formats
```bash
# Terminal report
pytest tests/ --cov=src --cov-report=term

# Just the summary
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Marker-Based Filtering

### Run Only Async Tests
```bash
pytest tests/ -m asyncio -v
```

### Run Only Regression Tests
```bash
pytest tests/integration/test_sbar_first.py -v -k "sbar"
```

---

## Parallel Execution

### Run Tests in Parallel (requires pytest-xdist)
```bash
pip install pytest-xdist

# 4 workers
pytest tests/ -n 4

# Auto-detect CPU count
pytest tests/ -n auto
```

---

## Specific Test Scenarios

### Test SBAR-First Behavior (Main Requirement)
```bash
pytest tests/integration/test_sbar_first.py -v

# Specific scenario
pytest tests/integration/test_sbar_first.py::TestSBARFirstBehavior::test_non_redflag_nurse_request_completes_intake_then_escalates -v
```

### Test Red-Flag Escalation
```bash
pytest tests/integration/test_sbar_first.py::TestEscalationTiming -v
```

### Test Prompt Injection Resilience
```bash
pytest tests/integration/test_adversarial.py::TestPromptInjection -v
```

### Test JSON Validation Retry Logic
```bash
pytest tests/unit/test_json_validation.py::TestRetryLogic -v
```

### Test Session Isolation
```bash
pytest tests/load/test_concurrent.py::TestSessionIsolation -v
```

### Test 50-Concurrent Sessions
```bash
pytest tests/load/test_concurrent.py::TestConcurrentSessions::test_50_concurrent_sessions -v
```

---

## Debugging

### Drop Into Debugger on Failure
```bash
pytest tests/ --pdb
```

### Show Local Variables on Error
```bash
pytest tests/ -l
```

### Full Traceback
```bash
pytest tests/ --tb=long
```

### Traceback Only on Failure
```bash
pytest tests/ --tb=short
```

---

## Configuration File

### pytest.ini
The repository includes a `pytest.ini` with sensible defaults:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
markers =
    asyncio: marks tests as async (deselect with '-m "not asyncio"')
    unit: marks tests as unit tests
    integration: marks tests as integration tests
    load: marks tests as load tests
addopts = -v
```

### Override Configuration
```bash
# Use different pytest file
pytest -c custom_pytest.ini
```

---

## Environment Variables

### Test Configuration
```bash
# Use mock LLM (recommended for testing)
export USE_MOCK_LLM=true

# Use in-memory storage
export STORAGE_BACKEND=memory

# Set environment to test
export ENVIRONMENT=test

# Enable PHI storage (test only, NEVER in production)
export STORE_PHI=false
```

### Run with Environment
```bash
USE_MOCK_LLM=true pytest tests/
```

---

## Golden Call Testing

### List Available Golden Calls
```bash
ls -la tests/golden_calls/
```

### Test Specific Golden Call
```bash
pytest tests/integration/test_sbar_first.py -k "GC_001" -v
```

### Validate Golden Call Format
```bash
# Quick validation script
python -m json.tool tests/golden_calls/GC_001_chest_pain_severe.json > /dev/null && echo "Valid JSON"
```

---

## CI/CD Integration

### GitHub Actions Example
```yaml
name: Phase 4 Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v --cov=src --cov-report=xml
      - uses: codecov/codecov-action@v3
```

### Jenkins Example
```groovy
pipeline {
    agent any
    stages {
        stage('Test') {
            steps {
                sh 'pip install -r requirements.txt'
                sh 'pytest tests/ -v --junit-xml=results.xml'
            }
        }
    }
    post {
        always {
            junit 'results.xml'
        }
    }
}
```

---

## Common Issues & Troubleshooting

### Issue: "No module named 'src'"
**Solution:** Run tests from repository root, not from tests/ directory
```bash
# Correct
cd /path/to/Nurse-Triage-Assistant
pytest tests/

# Incorrect
cd /path/to/Nurse-Triage-Assistant/tests
pytest  # Won't find src/
```

### Issue: "asyncio event loop is closed"
**Solution:** Ensure pytest-asyncio is installed
```bash
pip install pytest-asyncio>=0.21.0
```

### Issue: Golden calls not loading
**Solution:** Verify golden_calls directory exists and contains JSON files
```bash
ls -la tests/golden_calls/
file tests/golden_calls/GC_001_chest_pain_severe.json
```

### Issue: Memory error with load tests
**Solution:** Run load tests separately with memory limits
```bash
pytest tests/load/ -v --maxfail=1
```

### Issue: Slow tests
**Solution:** Run in parallel with pytest-xdist
```bash
pip install pytest-xdist
pytest tests/ -n auto
```

---

## Test Report Examples

### Summary Report
```bash
pytest tests/ -v --tb=short | tee test_report.txt
```

### JSON Report (requires pytest-json-report)
```bash
pip install pytest-json-report
pytest tests/ --json-report --json-report-file=report.json
```

### HTML Report (requires pytest-html)
```bash
pip install pytest-html
pytest tests/ --html=report.html
```

---

## Advanced Usage

### Run Tests in Watch Mode (requires pytest-watch)
```bash
pip install pytest-watch
ptw tests/
```

### Mock Server for Integration Tests
```bash
# Start mock LLM server (if needed)
python -m src.llm.mock_server
pytest tests/integration/
```

### Benchmark Performance
```bash
pip install pytest-benchmark
pytest tests/ --benchmark-only
```

---

## Performance Expectations

### Unit Tests
- Typical: <1 second total
- Max: <5 seconds

### Integration Tests  
- Typical: 5-15 seconds
- Max: <30 seconds

### Load Tests
- Typical: 10-60 seconds
- Max: <120 seconds

### Full Suite
- Typical: 20-90 seconds
- Max: <150 seconds

---

## Next Steps

1. **Run baseline:** `pytest tests/ -v` to ensure all tests pass
2. **Review coverage:** `pytest tests/ --cov=src --cov-report=html`
3. **Add golden calls:** Edit `tests/golden_calls/*.json` to test new scenarios
4. **Extend tests:** Add new test functions to validate new requirements
5. **Monitor CI:** Check test results in pull request checks

---

## Support

For issues with Phase 4 tests:
1. Check `docs/phase4/test_plan.md` for test categories
2. Check `docs/phase4/golden_dataset_spec.md` for case format
3. Review test files for examples and patterns
4. Check `docs/phase4/known_limitations.md` for constraints

