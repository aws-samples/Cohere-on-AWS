import os
import nbformat
import boto3
import json

#original code deployed by COurt Scheutt from Anthropic: https://github.com/aws-samples/anthropic-on-aws/blob/main/cookbooks/convert_to_bedrock/convert.py
#changing the prompt, but Cohere doesnt define their APis the exact same in the cookbooks. 


# Function to get the updated code using Bedrock
def get_updated_code(code, bedrock_rt):
    # Prepare the prompt for Bedrock
    prompt = f"""
    You are a software engineer working on a project to convert code that uses Anthropic SDK to AWS Bedrock.
    Please update the following Python code to use AWS Bedrock instead of Anthropic SDKs:

    <code>
    {code}
    </code>

    <instructions>
        - Provide only the updated code, without any explanations or additional text.
        - If no change is needed, do not make any changes.
        - The bedrock client is already initialized in the code as bedrock_rt
        - boto3 and json have already been imported
        - Do not include a ```python wrapper in the code
        - Use the same model when possible. 
    </instructions>

    <example>
    Here is an example using Cohere SDK:
    message = "Can you provide a sales summary for 29th September 2023, and also give me some details about the products in the 'Electronics' category, for example their prices and stock levels?"
    response = co.chat(
        message=message,
        tools=tools,
        preamble=preamble,
        model="command-r"
    )

    This is how the code should be updated to use AWS Bedrock:

    def converse_with_tools(messages, model_id):
        response = bedrock_rt.converse(
            messages=messages,
            additionalModelRequestFields=additional_model_fields,
            inferenceConfig={
            "temperature": 0.3
            },
            modelId=model_id
        )
        return response
        </example>
    """

    # Call Bedrock to update the code
    response = bedrock_rt.invoke_model(
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        accept="application/json",
        contentType="application/json",
    )

    # Extract the updated code from the Bedrock response
    response_body = json.loads(response.get("body").read())
    updated_code = response_body.get("content")[0].get("text")

    return updated_code


# Function to update the notebook content using Bedrock
def update_notebook(notebook, bedrock_rt):
    print(f"Updating notebook: {notebook.metadata.get('name')}")

    for cell in notebook.cells:
        if cell.cell_type == "code":
            # Update the pip install command
            if "pip install cohere" in cell.source:
                cell.source = cell.source.replace(
                    "pip install anthropic", "pip install boto3"
                )
                print("Updated pip install command.")

            if "import cohere" in cell.source:
                cell.source = cell.source.replace(
                    "import cohere", "import boto3\nimport json"
                )
                print("Updated import commands.")

            if "co = cohere.Client()" in cell.source:
                cell.source = cell.source.replace(
                    "co = cohere.Client()",
                    'bedrock_rt = boto3.client("bedrock-runtime", region_name="us-east-1")',
                )
                print("Updated client initialization.")

        
    return notebook


# Set up the Bedrock runtime client
bedrock_rt = boto3.client("bedrock-runtime", region_name="us-east-1")

# Directory containing the notebooks
input_directory = "../cohere-cookbooks/RAG"

# Recursively iterate through each file in the input directory and its subdirectories
for root, dirs, files in os.walk(input_directory):
    for filename in files:
        if filename.endswith(".ipynb"):
            notebook_path = os.path.join(root, filename)

            # Read the notebook
            with open(notebook_path, "r") as file:
                notebook = nbformat.read(file, as_version=4)

            # Update the notebook content using Bedrock
            updated_notebook = update_notebook(notebook, bedrock_rt)

            # Save the updated notebook in the same directory with a "bedrock" prefix
            output_filename = f"bedrock_{filename}"
            output_path = os.path.join(root, output_filename)
            with open(output_path, "w") as file:
                nbformat.write(updated_notebook, file)

            print(f"Updated notebook saved: {output_path}")

print("Notebook updates completed.")