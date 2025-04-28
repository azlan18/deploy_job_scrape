import asyncio
import re
import os
from flask import Flask, jsonify, request
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import json
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from groq import Groq

app = Flask(__name__)

# Get Groq API key from environment variables or use the hardcoded one
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")

# Initialize Groq client with your API key
client = Groq(api_key=GROQ_API_KEY)

# Backend configuration of URLs and selectors
job_sites = {
    "https://www.naukri.com/ola-jobs-careers-706807": ".srp-jobtuple-wrapper[data-job-id]",
    "https://www.naukri.com/swiggy-jobs?k=swiggy": ".srp-jobtuple-wrapper",
    "https://www.naukri.com/zepto-jobs?k=zepto&nignbevent_src=jobsearchDeskGNB": ".srp-jobtuple-wrapper"
}

async def fetch_html(url, selector, max_jobs=5):
    print(f"Starting to fetch HTML from URL: {url}")
    options = Options()
    options.add_argument("--headless")  # Run in headless mode for server deployment
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        # For server deployment, we need to handle ChromeDriver installation differently
        if os.environ.get('RENDER'):
            # On Render.com, Chrome is installed in a specific location
            chrome_path = '/usr/bin/google-chrome-stable'
            options.binary_location = chrome_path
            driver = webdriver.Chrome(options=options)
        else:
            # For local development, use webdriver_manager
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        
        print("WebDriver initialized and Chrome options set")
        
        print(f"Navigating to {url}")
        driver.get(url)
        print("Page navigation completed")
        
        print("Waiting for job listings to load")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        time.sleep(5)
        print("Job listings loaded, additional 5-second wait completed")

        try:
            print(f"Extracting up to {max_jobs} job-specific divs")
            job_elements = driver.find_elements(By.CSS_SELECTOR, selector)[:max_jobs]
            html_content = "".join([elem.get_attribute("outerHTML") for elem in job_elements])
            print(f"Job-specific HTML fetched, length: {len(html_content)}")
        except Exception as e:
            print(f"Failed to extract job divs: {e}. Saving full page anyway...")
            html_content = driver.page_source
            print(f"Full HTML fetched for debugging, length: {len(html_content)}")

        return html_content
    finally:
        print("Closing WebDriver")
        driver.quit()

def parse_jobs_with_llm(html_content, site_name):
    print(f"Starting to parse jobs with LLM for {site_name}")
    print(f"HTML content length sent to LLM: {len(html_content)}")
    prompt = f"""You are a highly capable AI model tasked with parsing HTML content and extracting structured data. The following is the raw HTML from a job board ({site_name}), containing job listing elements. Your task is to analyze this HTML and extract all available job listings into a JSON object. Use the following structure for the output:

{{
  "jobs": [
    {{
      "job_id": "string",
      "title": "string",
      "company": "string",
      "company_rating": "string",
      "company_reviews": "string",
      "experience": "string",
      "salary": "string",
      "location": "string",
      "description": "string",
      "skills": ["string", "string"],
      "posted": "string",
      "detail_url": "string",
      "logo_url": "string"
    }}
  ]
}}

For Naukri.com job listings:
- Extract the "job_id" from the data-job-id attribute of the div with class "srp-jobtuple-wrapper"
- Extract the "title" from the <a> element with class "title"
- Extract the "company" from the <a> element with class "comp-name"
- Extract the "company_rating" from the span with class "main-2" (inside the rating link)
- Extract the "company_reviews" from the <a> element with class "review"
- Extract the "experience" from the span with title containing "Yrs"
- Extract the "salary" from the span with title containing "Lacs PA" or any salary information
- Extract the "location" from the span with class "locWdth"
- Extract the "description" from the span with class "job-desc"
- Extract the "skills" as an array from the <li> elements inside <ul class="tags-gt">
- Extract the "posted" from the span with class "job-post-day"
- Extract the "detail_url" from the href attribute of the title <a> tag
- Extract the "logo_url" from the src attribute of the img with class "logoImage"

Only include jobs that have a valid title and company. If some fields are not explicitly available, use "Not specified".
Output the result as a valid JSON object with no additional text or explanations.

The HTML content is provided below:
{html_content}
"""

    print("Sending prompt to Groq API")
    completion = client.chat.completions.create(
        model="meta-llama/llama-4-maverick-17b-128e-instruct",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,  # Lower temperature for more deterministic parsing
        max_completion_tokens=4096,  # Increased token limit for more detailed extraction
        top_p=1,
        stream=False,
        response_format={"type": "json_object"},
        stop=None,
    )
    json_response = completion.choices[0].message.content
    print(f"Received LLM response: {json_response[:500]}...")  # Print first 500 chars to avoid flooding
    return json_response

async def scrape_all_jobs(max_jobs=5):
    all_jobs = {}
    errors = []

    for url, selector in job_sites.items():
        print(f"Processing {url}")
        site_name = url.split('/')[-1]
        
        try:
            html_content = await fetch_html(url, selector, max_jobs)
            print("HTML fetching completed")
            
            print("Starting LLM parsing")
            json_response = parse_jobs_with_llm(html_content, site_name)
            print("LLM parsing completed")
            
            cleaned_response = re.sub(r'^\s*```json\s*|\s*```$', '', json_response, flags=re.MULTILINE)
            
            try:
                print("Attempting to parse JSON response")
                jobs = json.loads(cleaned_response)
                all_jobs[site_name] = jobs["jobs"]
                print(f"JSON parsed successfully with {len(jobs['jobs'])} jobs")
            except json.JSONDecodeError as e:
                print(f"JSON parsing failed: {e}")
                errors.append(f"Failed to parse JSON for {site_name}: {str(e)}")
                continue
                
        except Exception as e:
            print(f"Error processing {site_name}: {str(e)}")
            errors.append(f"Error processing {site_name}: {str(e)}")
    
    result = {
        "success": len(errors) == 0,
        "jobs": all_jobs,
        "errors": errors
    }
    
    return result

@app.route('/scrape', methods=['GET'])
def scrape_jobs():
    max_jobs = request.args.get('max_jobs', default=3, type=int)
    result = asyncio.run(scrape_all_jobs(max_jobs))
    return jsonify(result)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "Service is running"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
