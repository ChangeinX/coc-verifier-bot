"""Nox configuration for testing and linting."""

import nox

# Define supported Python versions
nox.options.sessions = ["tests", "lint"]
python_versions = ["3.11", "3.12"]


@nox.session(python=python_versions[0])
def tests(session):
    """Run the test suite with coverage."""
    session.install("-e", ".[dev]")
    session.run(
        "pytest",
        "--cov=.",
        "--cov-report=term-missing",
        "--cov-report=html:htmlcov",
        "--cov-report=xml:coverage.xml",
        "--cov-fail-under=80",
        "-v",
        *session.posargs,
    )


@nox.session(python=python_versions[0])
def lint(session):
    """Run ruff for linting and formatting."""
    session.install("ruff>=0.1.0")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session(python=python_versions[0])
def format_code(session):
    """Format code with ruff."""
    session.install("ruff>=0.1.0")
    session.run("ruff", "format", ".")
    session.run("ruff", "check", "--fix", ".")


@nox.session(python=python_versions[0])
def coverage_report(session):
    """Generate detailed coverage report."""
    session.install("-e", ".[dev]")
    session.run(
        "pytest",
        "--cov=.",
        "--cov-report=term-missing",
        "--cov-report=html:htmlcov",
        "--cov-report=xml:coverage.xml",
        "--cov-branch",
        "-v",
        "--tb=short",
    )
    session.log("Coverage report generated in htmlcov/ directory")


@nox.session(python=python_versions[0])
def test_single(session):
    """Run a single test file or test function."""
    if not session.posargs:
        session.error("Please provide a test file or function to run")

    session.install("-e", ".[dev]")
    session.run("pytest", "-v", *session.posargs)
