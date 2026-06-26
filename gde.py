import requests

url = "http://models.ascend.huawei.com/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-aGZdUjqx9AO0S1GUhM8XV9ZN3334NPJ27wXpxQMbF0Bu2N8X"
}
data = {
    "model": "minimax2.7",
    "messages": [
        {"role": "user", "content": "Say hello in one sentence."}
    ]
}

response = requests.post(url, headers=headers, json=data)
print(response.json()["choices"][0]["message"]["content"])

"""
curl http://models.ascend.huawei.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-aGZdUjqx9AO0S1GUhM8XV9ZN3334NPJ27wXpxQMbF0Bu2N8X" \
  -d '{"model":"qwen3-vl-30b","messages":[{"role":"user","content":"Say hello in one sentence."}]}'
"""