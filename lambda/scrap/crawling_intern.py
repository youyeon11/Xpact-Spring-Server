from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import json
import boto3

# 기존의 모든 json 파일 로드
def load_all_json_ids_from_s3(bucket_name: str, prefix: str) -> set:
    s3 = boto3.client("s3")
    BUCKET_NAME = os.environ.get("S3_BUCKET")
    if not BUCKET_NAME:
        raise ValueError("환경 변수 'S3_BUCKET'이 설정되어 있지 않습니다.")

    all_ids = set()

    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        contents = response.get("Contents", [])

        for obj in contents:
            key = obj["Key"]
            if key.endswith(".json"):
                try:
                    res = s3.get_object(Bucket=bucket_name, Key=key)
                    data = json.load(res["Body"])
                    ids = {item["linkareer_id"] for item in data if "linkareer_id" in item}
                    all_ids.update(ids)
                except Exception as e:
                    print(f"S3에서 {key} 읽는 중 오류: {e}")

    except Exception as e:
        print(f"S3 파일 목록 조회 중 오류: {e}")

    return all_ids

# linkareer_id 추출을 위한 함수
def extract_link_id(link: str) -> int | None:
    prefix = "https://linkareer.com/activity/"
    try:
        return int(link.replace(prefix, ""))
    except ValueError:
        return None

# 각각의 link들 크롤링
def crawling_intern_link(driver):
    driver.get('https://linkareer.com/list/intern')
    links = []

    try:
        page = 1
        while True:
            print(f"크롤링 중: {page} 페이지")

            rows = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'tbody tr'))
            )

            for row in rows:
                try:
                    tds = row.find_elements(By.TAG_NAME, 'td')
                    if len(tds) > 1:
                        title_cell = tds[1]
                        href = title_cell.find_element(By.CSS_SELECTOR, 'a[href^="/activity/"]').get_attribute('href')
                        full_link = "https://linkareer.com" + href if href.startswith("/") else href
                        links.append(full_link)

                except StaleElementReferenceException:
                    print("상태 불안정 에러 발생. 건너뜁니다.")
                    continue

            # 다음 페이지로 이동
            try:
                next_page_number = str(page + 1)
                next_page_button = driver.find_element(
                    By.XPATH, f'//button[span[text()="{next_page_number}"]]'
                )
                next_page_button.click()
                WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'tbody tr'))
                )
                page += 1
                continue

            except NoSuchElementException:
                try:
                    next_arrow = driver.find_element(By.CSS_SELECTOR, 'button.button-arrow-next')
                    if next_arrow.get_attribute("disabled"):
                        print("마지막 페이지입니다.")
                        break

                    next_arrow.click()

                    WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'tbody tr'))
                    )

                    page += 1
                    continue

                except NoSuchElementException:
                    print("다음 버튼 없음. 종료합니다.")
                    break

                except ElementClickInterceptedException:
                    print("화살표 클릭 차단됨.")
                    break

            except ElementClickInterceptedException:
                print("페이지 번호 클릭 차단됨.")
                break

    except Exception as e:
        print(f"크롤링 중 예외 발생: {e}")

    finally:
        return links

# 구체적인 내용 (세부 내용) 크롤링
def crawling_intern_detail(link:str, driver) -> dict | None:
    
    # 예외 처리
    def safe_find_text(by, value) -> str | None:
        try:
            return driver.find_element(by, value).text
        except NoSuchElementException:
            return None
        
    try:
        driver.get(link)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'header.ActivityInformationHeader__StyledWrapper-sc-7bdaebe9-0 h1'))
        )

        title=safe_find_text(By.CSS_SELECTOR, 'header.ActivityInformationHeader__StyledWrapper-sc-7bdaebe9-0 h1')

        linkareer_id = extract_link_id(link)

        img_url = driver.find_element(By.CSS_SELECTOR, 'img.recruit-image').get_attribute('src')

        organizer_name = safe_find_text(By.CSS_SELECTOR, "h2.organization-name")
    
        enterprise_type = safe_find_text(By.XPATH, "//dl[dt[text()='기업형태']]/dd")
        
        job_category = safe_find_text(By.XPATH, "//dl[dt[text()='모집직무']]/dd")
        
        region = safe_find_text(By.XPATH, "//dl[dt[text()='근무지역']]/dd")
        
        raw_dates = safe_find_text(By.XPATH, "//dl[dt[text()='접수기간']]/dd")
    
        lines = raw_dates.split('\n')

        start_date = None
        end_date = None

        if len(lines) == 2:
            for line in lines:
                if '시작일' in line:
                    start_date = line.replace('시작일', '').strip()
                elif '마감일' in line:
                    end_date = line.replace('마감일', '').strip()
        
        homepage_url = safe_find_text(By.XPATH, "//dl[dt[text()='홈페이지']]/dd")
        if homepage_url in (None, "", "-"):
            homepage_url = link
        
        return {
            "title":title,
            "linkareer_id":linkareer_id,
            "organizer_name":organizer_name,
            "img_url":img_url,
            "enterprise_type":enterprise_type,
            "job_category":job_category,
            "region":region,
            "start_date":start_date,
            "end_date":end_date,
            "homepage_url":homepage_url,
            }

    except Exception as e:
        print(f"상세 페이지 오류: {e}")
        return None

# S3에 저장
def save_intern_to_s3(bucket_name: str, driver):

    # 기존의 데이터 불러오기
    try:
        old_ids = load_all_json_ids_from_s3(bucket_name, "data/INTERN")
    except Exception as e:
        old_ids = set()
        print("파일을 읽을 수 없습니다.", e)

    # 새로운 크롤링
    try:
        links = crawling_intern_link(driver)

        new_data = []
        for link in links:
            link_id = extract_link_id(link)
            if link_id is None or link_id in old_ids:
                print(f"이미 존재하는 공고입니다: {link_id}")
                continue

            data = crawling_intern_detail(link, driver)
            if data:
                new_data.append(data)

        return new_data
    except Exception as e:
        print(r"인턴 크롤링 중 오류 발생: {e}")
