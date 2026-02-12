from setuptools import find_packages, setup

setup(
    name="core",
    version="0.1.0",
    description="Shared core package for x-aout-agent",
    packages=find_packages(),
    install_requires=["SQLAlchemy>=2.0"],
)
