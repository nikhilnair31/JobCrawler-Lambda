import json
import os
import bs4
import boto3
import openai
import requests
import datetime
import traceback
from datetime import datetime, timedelta
from metaphor_python import Metaphor
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Loaded through .env
metaphor_api_key = os.environ.get('METAPHOR_API_KEY')
openai_api_key = os.environ.get('OPENAI_API_KEY')
metaphor = Metaphor(metaphor_api_key)
openai.api_key = openai_api_key

# Couldn't end up using SES for daily job updates becuase of AWS putting my newly setup identity into a sandbox
ses = boto3.client('ses')
def email(email, title, content, err):
    response = ses.send_email(
        Source = 'nikhil.nair@utexas.edu',
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': title},
            'Body': {'Text': {'Data': content}}
        }
    )

def metaphor_calls(query):
    start_date =  datetime.now() - timedelta(days=30)
    start_date_str = start_date.strftime('%Y-%m-%d')

    search_response = metaphor.search(
        query,

        use_autoprompt=True,
        num_results=3,
        type="neural",

        # include_domains=["github.com"], 
        # exclude_domains=["nytimes.com"],
        start_published_date=start_date_str, 
        # end_published_date="2023-06-25",
        start_crawl_date=start_date_str, 
        # end_crawl_date="2023-06-25",
    )
    # contents_result = search_response.get_contents()
    
    result_dicts = []
    for result in search_response.results:
        result_dict = {
            "Title": result.title,
            "URL": result.url,
            "ID": result.id,
            "Published Date": result.published_date,
            "Score": result.score
        }
        if result.author:
            result_dict["Author"] = result.author
        if result.extract:
            result_dict["Extract"] = result.extract
        result_dicts.append(result_dict)
    
    result_dicts_string = str(result_dicts)

    return result_dicts

def openai_calls(repos_lol, exp_level, jobrole):
    repo_names_descriptions_readmes_list = []
    for index, inner_list in enumerate(repos_lol, start=0):
        repo_names_descriptions_readmes_list.append(f"Repo {index + 1} - {inner_list[0]}: {inner_list[1]} : {inner_list[3]}")

    descriptions_str = '\n'.join(set(repo_names_descriptions_readmes_list))
    logger.info(f'descriptions_str: {descriptions_str}')

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": "You are a system that extracts domain specific keywords based on descriptions of the user's GitHub repos. Generate 5 keywords only. Print by order of frequency."
            },
            {
                "role": "user",
                "content": f"{descriptions_str}"
            }
        ],
        max_tokens=256,
    )
    summary = response.choices[0].message["content"]
    logger.info(f'summary: {summary}')

    completion = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
            "role": "system",
            "content":  f"""
                You generate search queries for {exp_level} experience job listings based on user programming experience and their job role of aim: {jobrole}.
                The prompt should be worded how someone may refer a link to someone else like in the examples provided. 
                Only generate one search query.
                Examples:
                - Here is a new data science job for you:
            """
            },
            {
            "role": "user",
            "content": summary
            },
        ],
    )
    query = completion.choices[0].message["content"].replace("\"", "").strip()
    return query

def github_scraper(url, base_url):
    r = requests.get(url)
    soup = bs4.BeautifulSoup(r.text, features="html.parser")

    repo_names = []
    repo_descs = []
    repo_dates = []
    repo_readm = []
    repo_stars = []

    for repo in soup.findAll('li', attrs={'class': 'col-12 d-flex flex-justify-between width-full py-4 border-bottom color-border-muted public source'}):
        rep = repo.find('a', attrs={'href': True, 'itemprop':'name codeRepository'})
        repo_path = rep['href']
        repo_name = repo_path.split('/')[2]
        repo_names.append(repo_name)

        descp = repo.find('p', attrs={'class': 'col-9 d-inline-block color-fg-muted mb-2 pr-4'})
        repo_desc = '-' if descp is None else descp.text.strip()
        repo_descs.append(repo_desc)

        dt = repo.find('relative-time', attrs={'datetime': True, 'class': 'no-wrap'})
        repo_date = dt['datetime']
        repo_dates.append(repo_date)

        readme_url = f"{base_url}{repo_path}/blob/main/README.md"
        response = requests.get(readme_url)
        if response.status_code == 200:
            repo_soup = bs4.BeautifulSoup(response.text, 'html.parser')

            titles = ' '.join([title.get_text() for title in repo_soup.find_all(['h1', 'h2'])])
            paragraphs = ' '.join([p.get_text() for p in repo_soup.find_all('p')])
            readme_section = titles + " " + paragraphs
            readme_content = readme_section.replace('\n', ' ').replace('MIT License', '').strip() if readme_section else '-'

            star_element = repo_soup.find("span", attrs = {"id": "repo-stars-counter-unstar"})
            star_count = star_element.get_text(strip=True) if star_element else 0
        else:
            readme_content = '-'
            star_count = 0
        repo_readm.append(readme_content)
        repo_stars.append(star_count)

    tem = list(zip(repo_names, repo_descs, repo_dates, repo_readm, repo_stars))
    logger.info(f'tem: {tem}')
    return tem

def determine_experience_level(repos_lol):
    # Convert tuples to lists and the 2nd index in the inner list to a date object
    new_repos_lol = [list(inner_list) for inner_list in repos_lol]
    for inner_list in new_repos_lol:
        inner_list[2] = datetime.strptime(inner_list[2], '%Y-%m-%dT%H:%M:%SZ')
    
    # Find the minimum date
    min_date = min(inner_list[2] for inner_list in new_repos_lol)
    print(f'min_date: {min_date}')
    
    total_experience = (datetime.today() - min_date).days / 365
    print(f'total_experience: {total_experience}')

    average_repos_per_year = len(repos_lol) / total_experience
    print(f'average_repos_per_year: {average_repos_per_year}')

    experience_level = 'beginner'
    if total_experience >= 1 and average_repos_per_year >= 10:
        experience_level = 'intermediate'
    elif total_experience >= 3 and average_repos_per_year >= 20:
        experience_level = 'experienced'

    return experience_level

def lambda_handler(event, context): 
    try:
        logger.info(f'started lambda_handler\n\n')

        # Set base URL
        base_url = "https://github.com/"
        # Pull username from API call but check if full URL or only username
        event_body_data = event["github"]
        if base_url in event_body_data:
            username = event_body_data.replace(base_url, '')
        else:
            username = event_body_data
        # Create full URL now
        url = f"{base_url}{username}?tab=repositories"
        logger.info(f'initial: {event_body_data} | {username} | {url}')

        github_lol = github_scraper(url, base_url)
        logger.info(f'github_lol: {github_lol}')

        exp_level = determine_experience_level(github_lol)
        logger.info(f'exp_level: {exp_level}')

        jobrole = event["jobrole"]
        query = openai_calls(github_lol, exp_level, jobrole)
        logger.info(f'query: {query}') 

        search_response = metaphor_calls(query)
        logger.info(f'\nfinal\n{search_response}\n') 

        # email(event["email"], 'Job Alerts Incoming!', search_response, None)
        
        return {
            'statusCode': 200,
            'headers':{
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers" : "Content-Type",
                "Access-Control-Allow-Methods" : "OPTIONS, POST",
                "Content-Type" : "application/json"
            },
            'body': json.dumps(search_response)
        }

    except Exception as e: 
        error_info = traceback.format_exc()
        logger.error(f'logging f max: \n{error_info}\n\n')
        
        # email(event["email"], 'Job Alerts Failed :(', '', error_info)
        
        return {
            'statusCode': 400,
            'body': f'Fail! {error_info}'
        }