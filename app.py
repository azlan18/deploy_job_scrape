import asyncio
import re
import os
import time
from flask import Flask, jsonify, request
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import json
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from groq import Groq
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Get Groq API key from environment variables or use the hardcoded one
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")

# Initialize Groq client with your API key
client = Groq(api_key=GROQ_API_KEY)

# Backend configuration of URLs and selectors
job_sites = {
    # "https://www.naukri.com/ola-jobs-careers-706807": ".srp-jobtuple-wrapper[data-job-id]",
    # "https://www.naukri.com/swiggy-jobs?k=swiggy": ".srp-jobtuple-wrapper",
    "https://www.naukri.com/zepto-jobs?k=zepto&nignbevent_src=jobsearchDeskGNB": ".srp-jobtuple-wrapper"
}

async def fetch_html(url, selector, max_jobs=3):
    start_time = time.time()
    print(f"Starting to fetch HTML from URL: {url}")
    options = Options()
    # Extensive optimizations for Chrome in a containerized environment
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument="--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-browser-side-navigation")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-web-security")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-in-process-stack-traces")
    options.add_argument("--disable-features=site-per-process")
    options.add_argument("--memory-pressure-off")
    options.add_argument("--js-flags=--max-old-space-size=256")  # Limit JavaScript heap
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    driver = None
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
        
        # Set page load timeout to prevent hanging
        driver.set_page_load_timeout(30)
        
        print(f"Navigating to {url}")
        try:
            driver.get(url)
            print("Page navigation completed")
        except Exception as e:
            print(f"Error during page load: {e}")
            # Return empty content if page load fails
            return ""
        
        print("Waiting for job listings to load")
        try:
            # Reduced wait time to prevent timeouts
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            print("Job listings found")
            # Reduced sleep time
            time.sleep(2)
        except Exception as e:
            print(f"Warning: Timeout waiting for elements: {e}")
            # Try to proceed anyway, maybe elements are there but condition wasn't met
            time.sleep(1)

        try:
            print(f"Extracting up to {max_jobs} job-specific divs")
            job_elements = driver.find_elements(By.CSS_SELECTOR, selector)[:max_jobs]
            if not job_elements:
                print("No job elements found with selector, trying to get page source")
                html_content = driver.page_source
            else:
                html_content = "".join([elem.get_attribute("outerHTML") for elem in job_elements])
            print(f"Job HTML fetched, length: {len(html_content)}")
            return html_content
        except Exception as e:
            print(f"Failed to extract job divs: {e}. Saving full page...")
            html_content = driver.page_source
            print(f"Full HTML fetched, length: {len(html_content)}")
            return html_content
    except Exception as e:
        print(f"Critical error in fetch_html: {str(e)}")
        return ""
    finally:
        if driver:
            print("Closing WebDriver")
            try:
                driver.quit()
            except Exception as e:
                print(f"Error closing driver: {e}")
        end_time = time.time()
        print(f"fetch_html took {end_time - start_time:.2f} seconds")

def parse_jobs_with_llm(html_content, site_name):
    start_time = time.time()
    if not html_content:
        print(f"No HTML content to parse for {site_name}")
        return json.dumps({"jobs": []})
        
    print(f"Starting to parse jobs with LLM for {site_name}")
    print(f"HTML content length: {len(html_content)}")
    
    # Truncate HTML if too large to prevent token issues
    max_html_length = 50000  # Approximately 12,500 tokens
    if len(html_content) > max_html_length:
        print(f"HTML content too large ({len(html_content)} chars), truncating to {max_html_length}")
        html_content = html_content[:max_html_length]
    
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

    try:
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
        print(f"Received LLM response: {json_response[:100]}...")  # Print just the beginning to avoid flooding logs
        return json_response
    except Exception as e:
        print(f"Error calling Groq API: {str(e)}")
        return json.dumps({"jobs": []})
    finally:
        end_time = time.time()
        print(f"parse_jobs_with_llm took {end_time - start_time:.2f} seconds")

async def scrape_single_site(url, selector, max_jobs=3):
    start_time = time.time()
    site_name = url.split('/')[-1]
    print(f"Processing {site_name}")
    
    try:
        html_content = await fetch_html(url, selector, max_jobs)
        if not html_content:
            return site_name, [], f"Failed to fetch HTML for {site_name}"
            
        print("HTML fetching completed for", site_name)
        
        json_response = parse_jobs_with_llm(html_content, site_name)
        print("LLM parsing completed for", site_name)
        
        cleaned_response = re.sub(r'^\s*```json\s*|\s*```$', '', json_response, flags=re.MULTILINE)
        
        try:
            print("Attempting to parse JSON response for", site_name)
            jobs = json.loads(cleaned_response)
            return site_name, jobs["jobs"], None
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON for {site_name}: {str(e)}"
            print(error_msg)
            return site_name, [], error_msg
                
    except Exception as e:
        error_msg = f"Error processing {site_name}: {str(e)}"
        print(error_msg)
        return site_name, [], error_msg
    finally:
        end_time = time.time()
        print(f"scrape_single_site took {end_time - start_time:.2f} seconds")

async def scrape_all_jobs(max_jobs=3):
    start_time = time.time()
    all_jobs = {}
    errors = []
    
    # Process sites concurrently to speed up execution
    tasks = []
    for url, selector in job_sites.items():
        tasks.append(scrape_single_site(url, selector, max_jobs))
    
    # Process up to 2 sites concurrently
    results = await asyncio.gather(*tasks)
    
    for site_name, jobs, error in results:
        if error:
            errors.append(error)
        if jobs:
            all_jobs[site_name] = jobs
    
    result = {
        "success": len(errors) == 0,
        "jobs": all_jobs,
        "errors": errors
    }
    
    end_time = time.time()
    print(f"scrape_all_jobs took {end_time - start_time:.2f} seconds")
    return result

@app.route('/scrape', methods=['GET'])
def scrape_jobs():
    start_time = time.time()
    max_jobs = request.args.get('max_jobs', default=2, type=int)
    # Limit max_jobs to prevent resource exhaustion
    max_jobs = min(max_jobs, 5)
    
    try:
        # Set a timeout for the entire scraping operation
        result = asyncio.run(scrape_all_jobs(max_jobs))
        end_time = time.time()
        print(f"scrape_jobs took {end_time - start_time:.2f} seconds")
        return jsonify(result)
    except Exception as e:
        end_time = time.time()
        print(f"scrape_jobs took {end_time - start_time:.2f} seconds")
        return jsonify({
            "success": False,
            "jobs": {},
            "errors": [f"Server error: {str(e)}"]
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "Service is running"})

# Add a simple endpoint to check if Chrome is working
@app.route('/test-chrome', methods=['GET'])
async def test_chrome():
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        if os.environ.get('RENDER'):
            chrome_path = '/usr/bin/google-chrome-stable'
            options.binary_location = chrome_path
            driver = webdriver.Chrome(options=options)
        else:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            
        driver.get("https://www.google.com")
        title = driver.title
        driver.quit()
        
        return jsonify({
            "success": True,
            "message": "Chrome is working",
            "title": title
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Chrome test failed: {str(e)}"
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
