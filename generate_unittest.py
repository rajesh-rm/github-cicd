import os
import json
import subprocess
import shutil
import requests
import ast
from pathlib import Path
from coverage import Coverage

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
    """Analyze interdependencies using the ast module."""
    dependencies = {}

    # Walk through all Python files in the repository
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as source_file:
                    try:
                        # Parse the file content into an AST
                        tree = ast.parse(source_file.read(), filename=file_path)
                        imports = []

                        # Iterate over all nodes in the AST
                        for node in ast.walk(tree):
                            # Check for import statements
                            if isinstance(node, ast.Import):
                                for alias in node.names:
                                    imports.append(alias.name)
                            elif isinstance(node, ast.ImportFrom):
                                if node.module:
                                    imports.append(node.module)

                        # Store the imports in the dependencies dictionary
                        module_name = os.path.relpath(file_path, repo_path).replace(os.sep, ".")[:-3]  # Remove .py extension
                        dependencies[module_name] = imports
                    except SyntaxError as e:
                        print(f"Syntax error in file {file_path}: {e}")
                    except Exception as e:
                        print(f"Error processing file {file_path}: {e}")

    return dependencies

def extract_function_metadata(file_path):
    """Extract detailed function metadata using ast."""
    metadata = []
    with open(file_path, "r", encoding="utf-8") as source_file:
        tree = ast.parse(source_file.read(), filename=file_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                metadata.append({
                    "name": node.name,
                    "args": [arg.arg for arg in node.args.args],
                    "docstring": ast.get_docstring(node),
                })
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

if __name__ == "__main__":
    main()
