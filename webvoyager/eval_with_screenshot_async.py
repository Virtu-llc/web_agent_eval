import json
import os
import dotenv
import asyncio
from typing import List, Dict, Any

from openai import AsyncOpenAI
import base64

dotenv.load_dotenv()


client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

async def eval(task, txt_file, img_file):
    with open(txt_file, "r") as f:
        response = f.read()

    SYSTEM_PROMPT = '''
     As an evaluator, you will be presented with three primary components to assist you in your role:

    1. Web Task Instruction: This is a clear and specific directive provided in natural language, detailing the online activity to be carried out. These requirements may include conducting searches, verifying information, comparing prices, checking availability, or any other action relevant to the specified web service (such as Amazon, Apple, ArXiv, BBC News, Booking etc).
    
    2. Result Screenshots: This is a visual representation of the screen showing the result or intermediate state of performing a web task. It serves as visual proof of the actions taken in response to the instruction, and may not represent everything the agent sees.
    
    3. Result Response: This is a textual response obtained after the execution of the web task. It serves as textual result in response to the instruction.
    
    -- You DO NOT NEED to interact with web pages or perform actions such as booking flights or conducting searches on websites.
    -- You SHOULD NOT make assumptions based on information not presented in the screenshot when comparing it to the instructions. If you cannot find any information in the screenshot that matches the instruction, you can believe the information in the response.
    -- Your primary responsibility is to conduct a thorough assessment of the web task instruction against the outcome depicted in the screenshot and in the response, evaluating whether the actions taken align with the given instructions.
    -- NOTE that the instruction may involve more than one task, for example, locating the garage and summarizing the review. Failing to complete either task, such as not providing a summary, should be considered unsuccessful.
    -- NOTE that the screenshot is authentic, but the response provided by LLM is generated at the end of web browsing, and there may be discrepancies between the text and the screenshots.
    
    ## Note the difference: 
    1) Result response may contradict the screenshot, then the content of the screenshot prevails, 
    2) The content in the Result response is not mentioned on the screenshot, choose to believe the content.
    3) If you are not sure whether you should believe the content in the response, you should choose unknown.
    
    You should elaborate on how you arrived at your final evaluation and then provide a definitive verdict on whether the task has been successfully accomplished, either as 'SUCCESS', 'NOT SUCCESS', or 'UNKNOWN'.
    
    IMPORTANT CONTEXT:
    - Today’s date is Sep 2025. newer info may be unknown to you.  
    
    return your response in the following json format:
    {
        "result": "SUCCESS" or "FAILED" or  "UNKNOWN",
        "reason": "evaluation reason",
    }
    """
    '''

    USER_PROMPT = f'''TASK: {json.dumps(task)}
    Result Response: {response}
    '''

    USER_MESSAGE_CONTENT = [
        {
            'type': 'input_text',
            'text': USER_PROMPT,
        },
    ]
    if img_file:
        base_64_img = encode_image(img_file)
        USER_MESSAGE_CONTENT.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{base_64_img}",

        })

    messages = [
        {
            'role': 'system',
            'content': SYSTEM_PROMPT,
        },
        {
            'role': 'user',
            'content': USER_MESSAGE_CONTENT,

        }
    ]

    try:
        response = await client.responses.create(
            model='gpt-4o',
            input=messages,
            text={"format": {"type": "json_object"}}
        )
        result = json.loads(response.output_text)
        webname = task['web_name']
        id = task['id']
        result = {"id": id, "webname": webname, **result}
        return result
    except Exception as e:
        print(f"Error evaluating task {task.get('id', 'unknown')}: {str(e)}")
        return {
            "id": task.get('id', 'unknown'),
            "webname": task.get('web_name', 'unknown'),
            "result": "ERROR",
            "reason": f"Evaluation failed: {str(e)}"
        }


def write_batch_to_file(results: List[Dict[Any, Any]], output_file: str):
    """Write a batch of results to the output file"""
    with open(output_file, 'a', encoding='utf-8') as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
    print(f"Wrote {len(results)} results to {output_file}")

async def process_batch(tasks_batch: List[Dict[Any, Any]], dir_path: str) -> List[Dict[Any, Any]]:
    """Process a batch of tasks asynchronously"""
    tasks = []
    for task in tasks_batch:
        id = task['id']
        file = f'{dir_path}/{id}.txt'
        img_dir = f'{file}'.replace('.txt', '/')
        
        # Find screenshot file
        img_path = None
        try:
            if os.path.exists(img_dir):
                imgs = os.listdir(img_dir)
                for img in imgs:
                    if img.endswith('.png'):
                        img_path = f'{img_dir}{img}'
                        break
        except Exception as e:
            print(f"Error accessing image directory for task {id}: {str(e)}")
        
        print(f"Processing task {id}: {file}, {img_path}")
        tasks.append(eval(task, file, img_path))
    
    # Execute all tasks in the batch concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Handle exceptions in results
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            task = tasks_batch[i]
            print(f"Exception for task {task.get('id', 'unknown')}: {str(result)}")
            processed_results.append({
                "id": task.get('id', 'unknown'),
                "webname": task.get('web_name', 'unknown'),
                "result": "ERROR",
                "reason": f"Exception occurred: {str(result)}"
            })
        else:
            processed_results.append(result)
    
    return processed_results

async def main():
    """Main async function to process all tasks in batches"""
    DIR = 'results'
    BATCH_SIZE = 10
    OUTPUT_FILE = 'webvoyager_eval.jsonl'
    
    # Load impossible tasks
    with open("WebVoyagerImpossibleTasks.json") as f:
        impossible_ids = json.load(f)
    
    # Load all tasks
    tasks_to_process = []
    with open('WebVoyager_data.jsonl', 'r') as f:
        lines = f.readlines()
        for line in lines:
            if line.strip():
                task = json.loads(line)
                id = task['id']
                if id not in impossible_ids:
                    tasks_to_process.append(task)
    
    print(f"Total tasks to process: {len(tasks_to_process)}")
    
    # Process tasks in batches
    all_results = []
    for i in range(0, len(tasks_to_process), BATCH_SIZE):
        batch = tasks_to_process[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(tasks_to_process) + BATCH_SIZE - 1) // BATCH_SIZE
        
        print(f"\nProcessing batch {batch_num}/{total_batches} ({len(batch)} tasks)")
        
        # Process the batch
        batch_results = await process_batch(batch, DIR)
        all_results.extend(batch_results)
        
        # Write batch results to file immediately
        write_batch_to_file(batch_results, OUTPUT_FILE)
        
        print(f"Completed batch {batch_num}/{total_batches}")
    
    print(f"\nAll processing completed! Total results: {len(all_results)}")
    print(f"Results written to: {OUTPUT_FILE}")


def score():
    with open('webvoyager_eval.jsonl', 'r') as f:
        total = 0
        total_success = 0
        total_failed = 0

        groups = dict()
        rows = f.readlines()
        for row in rows:
            result = json.loads(row)
            webname = result['webname']
            id = result['id']
            status = result['result']
            total += 1
            if webname not in groups:
                groups[webname] = {'success': 0, 'failed': 0}
            if status == 'SUCCESS':
                total_success += 1
                groups[webname]['success'] += 1
            else:
                total_failed += 1
                groups[webname]['failed'] += 1
        accuracy = total_success / total
        group_accuracy = dict()
        for webname in groups:
            success = groups[webname].get('success', 0)
            failed = groups[webname].get('failed', 0)
            group_accuracy[webname] = success / (success + failed)

        print('Web Agent accuracy:', accuracy)
        print('Web Agent accuracy by website:')
        for webname in group_accuracy:
            print('\t', webname, ':', group_accuracy[webname])



if __name__ == "__main__":
    # asyncio.run(main())
    score()
