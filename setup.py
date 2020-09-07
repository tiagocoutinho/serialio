# -*- coding: utf-8 -*-

"""The setup script."""

import sys
from setuptools import setup, find_packages


TESTING = any(x in sys.argv for x in ["test", "pytest"])

setup_requirements = ["bumpversion"]
if TESTING:
    if sys.version_info < (3, 7):
        print("testing serialio needs python >= 3.7")
        exit(1)
    setup_requirements += ["pytest-runner"]
test_requirements = ["pytest", "pytest-cov", "pytest-asyncio"]

with open("README.md") as f:
    description = f.read()

setup(
    name="serialio",
    author="Jose Tiago Macara Coutinho",
    author_email="coutinhotiago@gmail.com",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    description="Concurrency agnostic serialio API",
    license="GPLv3+",
    install_requires=["pyserial", "sockio>=0.10"],
    long_description=description,
    long_description_content_type="text/markdown",
    keywords="serial, rs232, rcf2217, socket, tcp, ser2net",
    packages=find_packages(),
    setup_requires=setup_requirements,
    test_suite="tests",
    tests_require=test_requirements,
    url="https://tiagocoutinho.github.io/serialio/",
    version="2.0.0",
    python_requires=">=3.5",
    zip_safe=True,
)
