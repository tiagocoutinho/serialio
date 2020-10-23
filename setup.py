# -*- coding: utf-8 -*-

"""The setup script."""

import sys
from setuptools import setup, find_packages

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
        "Programming Language :: Python :: 3.9",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)"
    ],
    description="Concurrency agnostic serialio API",
    license="GPLv3+",
    install_requires=["pyserial", "sockio>=0.10"],
    extras_require={
        "tango": ["pytango"]
    },
    long_description=description,
    long_description_content_type="text/markdown",
    keywords="serial, rs232, rcf2217, socket, tcp, ser2net",
    packages=find_packages(),
    url="https://tiagocoutinho.github.io/serialio/",
    version="2.2.2",
    python_requires=">=3.5",
    zip_safe=True,
)
