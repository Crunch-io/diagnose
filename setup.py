from setuptools import find_packages, setup

ext_modules = []


setup(
    name="probes",
    version="1.0.0",
    author="Robert Brewer",
    author_email="dev@crunch.io",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    install_requires=["hunter>=2.2.0", "mock"],
    ext_modules=ext_modules,
    entry_points={},
)
