import os
import json
import subprocess
import shutil
import requests
import astroid
from pathlib import Path
from coverage import Coverage
from modulegraph.modulegraph import ModuleGraph

# Configuration
GIT_REPO_PATH = "path/to/your/repo"  # Replace with your Git repo path
UNIT_TESTS_FOLDER = os.path.join(GIT_REPO_PATH, "unit_tests")
METADATA_FILE = os.path.join(GIT_REPO_PATH, "metadata.json")
LOG_FILE = os.path.join(GIT_REPO_PATH, "test_failure_log.json")
MAX_ITERATIONS = 3

# Azure OpenAI Configuration
AZURE_OPENAI_API_KEY = "your-azure-openai-api-key"
AZURE_OPENAI_ENDPOINT = "https://<your-resource-name>.openai.azure.com/"
AZURE_OPENAI_DEPLOYMENT_NAME = "gpt-4"  # Replace with your deployment name
AZURE_API_VERSION = "2024-05-01-preview"

def call_azure_openai(deployment_name, prompt, max_tokens=700, temperature=0.4, frequency_penalty=0.0, presence_penalty=0.0):
    """Call Azure OpenAI API for text completion."""
    url = f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{deployment_name}/chat/completions?api-version={AZURE_API_VERSION}"
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_API_KEY,
    }
    data = {
        "messages": [{"role": "system", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        raise Exception(f"Azure OpenAI API call failed: {response.status_code} - {response.text}")


def analyze_dependencies(repo_path):
    """Analyze interdependencies using modulegraph."""
    graph = ModuleGraph()
    graph.run_script(os.path.join(repo_path, "__main__.py"))
    dependencies = {mod.identifier: list(mod.imports.keys()) for mod in graph.flatten()}
    return dependencies


def extract_function_metadata(file_path):
    """Extract detailed function metadata using astroid."""
    metadata = []
    try:
        module = astroid.parse(Path(file_path).read_text())
        for func in module.body:
            if isinstance(func, astroid.FunctionDef):
                metadata.append({
                    "name": func.name,
                    "args": [arg.name for arg in func.args.args],
                    "docstring": func.doc_node and func.doc_node.value,
                    "annotations": {arg.name: arg.annotation.as_string() if arg.annotation else None
                                    for arg in func.args.args},
                })
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
    return metadata


def create_metadata(repo_path, metadata_file):
    """Generate metadata for the codebase."""
    metadata = {"files": {}, "dependencies": analyze_dependencies(repo_path)}
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("test_"):
                file_path = os.path.join(root, file)
                metadata["files"][os.path.relpath(file_path, repo_path)] = extract_function_metadata(file_path)

    with open(metadata_file, "w") as file:
        json.dump(metadata, file, indent=4)
    print(f"Metadata created at {metadata_file}")


def generate_unit_tests(metadata, output_folder):
    """Generate unit tests using Azure OpenAI."""
    os.makedirs(output_folder, exist_ok=True)
    for file_path, functions in metadata["files"].items():
        with open(os.path.join(GIT_REPO_PATH, file_path), "r") as file:
            file_content = file.read()

        for function in functions:
            function_name = function["name"]
            print(f"Generating test for {function_name} in {file_path}...")

            # Create a context-aware prompt with best practices
            prompt = f"""
You are an expert Python developer. Generate robust unit tests for the following function in the given file:
- Function name: {function_name}
- Arguments: {function['args']}
- Type annotations: {function.get('annotations', 'None')}
- Docstring: {function.get('docstring', 'None')}

Here is the full file context for reference:
{file_content}

Ensure the tests follow these best practices:
- The Arrange-Act-Assert pattern.
- One logical assertion per test case.
- Include edge cases (e.g., empty inputs, boundary values, invalid inputs).
- Use parameterized tests for multiple input scenarios.
- Mock external dependencies where applicable.
- Write meaningful assertions that validate behavior, even if the code implementation does not currently comply.
- Ensure the unit tests are well-designed, regardless of code quality.
"""

            test_code = call_azure_openai(
                deployment_name=AZURE_OPENAI_DEPLOYMENT_NAME,
                prompt=prompt,
                max_tokens=700,
                temperature=0.4,
            )

            # Save the generated test
            test_file_name = f"test_{os.path.basename(file_path)}"
            test_file_path = os.path.join(output_folder, test_file_name)
            with open(test_file_path, "a") as file:
                file.write(test_code)
                file.write("\n\n")
            print(f"Test saved to {test_file_path}")


def validate_and_log_tests(test_folder):
    """Validate tests and separate issues into code compliance or test design."""
    failure_log = {"test_issues": [], "code_issues": []}
    cov = Coverage()
    cov.start()

    for test_file in os.listdir(test_folder):
        if test_file.startswith("test_") and test_file.endswith(".py"):
            test_path = os.path.join(test_folder, test_file)
            for attempt in range(MAX_ITERATIONS):
                print(f"Running tests for {test_path} (Attempt {attempt + 1})...")
                result = subprocess.run(["python", "-m", "unittest", test_path], capture_output=True, text=True)

                if result.returncode == 0:
                    print(f"Tests passed for {test_file}")
                    break
                else:
                    error_message = result.stderr
                    print(f"Test failed with error: {error_message}")

                    if "AssertionError" in error_message:
                        # Log as a code compliance issue
                        failure_log["code_issues"].append({
                            "test_file": test_path,
                            "error": error_message,
                            "attempt": attempt + 1,
                        })
                        print(f"Logged as a code compliance issue for review.")
                        break  # Do not modify the test for AssertionErrors
                    else:
                        # Log and refine test logic issues
                        failure_log["test_issues"].append({
                            "test_file": test_path,
                            "error": error_message,
                            "attempt": attempt + 1,
                        })

                        # Generate a new test using error feedback
                        prompt = f"""
The following test has a structural or logic issue, not related to code compliance:
{error_message}

Refactor and fix the test to ensure it adheres to best practices and runs successfully. Here's the test file:
{open(test_path).read()}
"""
                        fixed_test_code = call_azure_openai(
                            deployment_name=AZURE_OPENAI_DEPLOYMENT_NAME,
                            prompt=prompt,
                            max_tokens=700,
                            temperature=0.4 + (0.1 * attempt),
                        )

                        # Overwrite the test file with the new code
                        with open(test_path, "w") as file:
                            file.write(fixed_test_code)

    cov.stop()
    cov.save()
    print("Coverage report:")
    cov.report()

    # Save failure log
    with open(LOG_FILE, "w") as log_file:
        json.dump(failure_log, log_file, indent=4)
    print(f"Failure log saved at {LOG_FILE}")


def main():
    # Step 1: Create Metadata
    create_metadata(GIT_REPO_PATH, METADATA_FILE)

    # Step 2: Load Metadata
    with open(METADATA_FILE, "r") as file:
        metadata = json.load(file)

    # Step 3: Generate Unit Tests
    if os.path.exists(UNIT_TESTS_FOLDER):
        shutil.rmtree(UNIT_TESTS_FOLDER)
    generate_unit_tests(metadata, UNIT_TESTS_FOLDER)

    # Step 4: Validate Tests and Log Failures
    validate_and_log_tests(UNIT_TESTS_FOLDER)


if __name__ == "__main__":
    main()
