"""Setup configuration for stress_recovery_cycle package."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="stress-recovery-cycle-measurement",
    version="1.0.0",
    author="Veronica GaoZhan",
    author_email="",
    description="Two-phase stress-recovery cycling tests of optical devices using B1500 and Thorlabs power meters",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/vvvvvero/VCSEL_Reliablity_Tests_3_LIV_Stress_Recovery",
    packages=find_packages(exclude=["tests", "examples"]),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Topic :: Scientific/Engineering",
        "Development Status :: 4 - Beta",
    ],
    python_requires=">=3.8",
    install_requires=[
        "PyQt5>=5.15",
        "numpy>=1.19",
        "matplotlib>=3.3",
        "pyvisa>=1.11",
        "pyvisa-py>=0.5",
    ],
    entry_points={
        "console_scripts": [
            "stress-recovery-cycle=stress_recovery_cycle.main:main",
        ],
    },
)
