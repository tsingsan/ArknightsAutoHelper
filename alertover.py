import sys
import requests

if __name__ == '__main__':

    
    if len(sys.argv) > 1:
        requests.post(
            "https://api.alertover.com/v1/alert",
            data={
                "source": "s-42721009-a675-4738-b05c-57f50d77",
                "receiver": "u-516be050-0da6-4144-8d52-6eb70bbe",
                "urgency": True,
                "content": sys.argv[1],
            }
        )
    requests.post(
            "https://api.alertover.com/v1/alert",
            data={
                "source": "s-e91f93fc-40d7-4f1c-bdae-7de229d7",
                "receiver": "g-4bb5ab90-25a9-4ab3-936f-91a6363f",
                "urgency": False,
                "content": "not important",
            }
        )