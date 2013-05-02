#!/usr/bin/env python
################################################################################
# Build script for slick-reporter
################################################################################

__author__ = 'Jason Corbett'

import distribute_setup
distribute_setup.use_setuptools()

from setuptools import setup, find_packages

setup(
    name="slick-reporter",
    description="A command line utility that can run other commands and turn their output into slick results.",
    version="1.0" + open("build.txt").read(),
    license="License :: OSI Approved :: Apache Software License",
    long_description=open('README.txt').read(),
    packages=find_packages(exclude=['distribute_setup']),
    package_data={'': ['*.txt', '*.rst', '*.html']},
    include_package_data=True,
    install_requires=['slickqa>=2.0.16',],
    author="Slick Developers",
    url="http://code.google.com/p/slickqa",
    entry_points={
        'console_scripts': ['slick-reporter = slickreporter:main',],
    }
)



