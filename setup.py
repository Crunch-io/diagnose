from setuptools import find_packages, setup

ext_modules = []

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    python_requires=">=3.6",
    name="diagnose",
    version="5.0.1",
    author="Robert Brewer",
    author_email="dev@crunch.io",
    description="A library for instrumenting Python code at runtime.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Crunch-io/diagnose",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    ext_modules=ext_modules,
    entry_points={},
    install_requires=["hunter>=2.2.0"],
)
