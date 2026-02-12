from setuptools import find_packages, setup

setup(
    name='x-aout-core',
    version='0.1.0',
    description='Shared core package for x-aout-agent',
    packages=find_packages(include=['core', 'core.*']),
)
