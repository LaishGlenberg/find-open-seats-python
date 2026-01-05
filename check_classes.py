import requests
from datetime import datetime
import time
from flask import Flask
import threading

app = Flask(__name__)
last_run_time = time.time()

@app.route('/health')
def health_check():
    time_since_last_run = time.time() - last_run_time
    return {
        'status': 'healthy' if time_since_last_run < 600 else 'stalled',  # 10 minute threshold
        'seconds_since_last_run': round(time_since_last_run, 2),
        'last_run_timestamp': last_run_time
    }

def start_health_server():
    app.run(host='0.0.0.0', port=5000, debug=False)

# Start health server
health_thread = threading.Thread(target=start_health_server, daemon=True)
health_thread.start()


CHECK_INTERVAL_MINUTES = 8  # How often to check
TOPIC = 'asu-alerts'  # ntfy topic
MAX_NOTIFICATIONS_PER_CLASS = 6  # Total notifications (every hour) before stopping

CLASS_SEARCH_NAME = ['CSE 486']  # Add more as needed
TERM_NUMBER = '2261'

WHITELIST = []
BLACKLIST_LOCATIONS = {"ASUONLINE"}
BLACKLIST_CLASS_NUMBERS = {"19134"}

BASE_API_URL = 'https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes'

HEADERS = {
    'Authorization': 'Bearer null'
}

# Build URLs for each class
URLS = []
for item in CLASS_SEARCH_NAME:
    subject, catalogNbr = item.split(' ')
    params = {
        "refine": "Y",
        "campusOrOnlineSelection": "A",
        "catalogNbr": catalogNbr,
        "honors": "F",
        "promod": "F",
        "searchType": "all",
        "subject": subject,
        "term": TERM_NUMBER
    }
    url = f"{BASE_API_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    URLS.append({'url': url, 'className': item})

notify_tracker = {}  # { classNumber: { lastSent: timestamp, interval: hours, notificationCount: int } }

def fetch_class_data(url, class_name):
    global last_run_time

    try:
        res = requests.get(url, headers=HEADERS)
        data = res.json()
    except Exception as e:
        print(f"Error fetching data for {class_name}: {e}")
        return

    for item in data.get('classes', []):
        class_info = item.get('CLAS', {})
        class_number = class_info.get('CLASSNBR', '')
        seat_info = item.get('seatInfo', {})
        if not WHITELIST or class_number in WHITELIST:
            name = class_name
            title = class_info.get('TITLE', '')
            instructor = ', '.join(class_info.get('INSTRUCTORSLIST', []))
            location = class_info.get('LOCATION', '')

            enrolled = seat_info.get('ENRL_TOT', '')
            total_seats = seat_info.get('ENRL_CAP', '')
            enrolled2 = class_info.get('ENRLTOT', '')
            total_seats2 = class_info.get('ENRLCAP', '')

            if location in BLACKLIST_LOCATIONS:
                continue

            print(f"{enrolled2}/{total_seats2}")

            try:
                enrolled_num = int(enrolled)
                total_seats_num = int(total_seats)

                enrolled_num2 = int(enrolled2)
                total_seats_num2 = int(total_seats2)
            except ValueError:
                enrolled_num = total_seats_num = enrolled_num2 = total_seats_num2 = 0

            tracker = notify_tracker.get(class_number, {'lastSent': 0, 'interval': 1, 'notificationCount': 0, 'possiblyOpenNotificationCount': 0})
            now = int(time.time() * 1000)
            next_send = tracker['lastSent'] + tracker['interval'] * 60 * 60 * 1000

            fully_open = possibly_open = False
            if enrolled_num2 < total_seats_num2:
                possibly_open = True
            if enrolled_num < total_seats_num:
                fully_open = True

            if fully_open:
                if tracker['notificationCount'] >= MAX_NOTIFICATIONS_PER_CLASS:
                    print(f"Max notifications ({MAX_NOTIFICATIONS_PER_CLASS}) reached for class {class_number} ({title}). Skipping.")
                elif now >= next_send:
                    message = f"OPEN SEAT: {name}\n{title}\nInstructor: {instructor}\nLocation: {location}\nNumber: {class_number}"
                    print(message)
                    try:
                        requests.post(f"https://ntfy.sh/{TOPIC}", data=message.encode('utf-8'))
                    except Exception as e:
                        print(f"Error sending notification: {e}")
                    print(f"Next notification for class {class_number} in {tracker['interval']} hour(s). ({tracker['notificationCount'] + 1}/{MAX_NOTIFICATIONS_PER_CLASS})\n")
                    notify_tracker[class_number] = {
                        'lastSent': now,
                        'interval': 1,
                        'notificationCount': tracker['notificationCount'] + 1
                    }
                else:
                    mins_left = int((next_send - now) / (60 * 1000)) + 1
                    print(f"Skipping notification for class {class_number} ({title}), next notification in {mins_left} min(s). ({tracker['notificationCount']}/{MAX_NOTIFICATIONS_PER_CLASS})")
            else:
                if notify_tracker.get(class_number, {}).get('notificationCount', 0) > 0:
                    print(f"Seats closed for class {class_number} ({title}), resetting notification count.")
                    notify_tracker[class_number] = {'lastSent': 0, 'interval': 1, 'notificationCount': 0}
                else:
                    print(f"No open seats: ({class_number}) {name}, {title}, Instructor: {instructor}, Location: {location}, Seats: {enrolled} of {total_seats}\n")    

            last_run_time = time.time()

def run_all_checks():
    time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{time}] Checking seats:')
    for entry in URLS:
        fetch_class_data(entry['url'], entry['className'])
    print(f"Next check in {CHECK_INTERVAL_MINUTES} minutes...\n")

if __name__ == "__main__":
    while True:
        run_all_checks()
        time.sleep(CHECK_INTERVAL_MINUTES * 60)
