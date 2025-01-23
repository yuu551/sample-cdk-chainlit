
from decimal import Decimal
import io
import os
import re
from base64 import b64encode
import boto3
from PIL import Image
import chainlit as cl
from chainlit.input_widget import Select, Slider
from langchain.memory import ConversationBufferMemory
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.schema import StrOutputParser
from langchain.schema.runnable import RunnablePassthrough, RunnableLambda
from langchain.schema.runnable.config import RunnableConfig
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage
import chainlit.data as cl_data
from chainlit.data.dynamodb import DynamoDBDataLayer
from chainlit.data.storage_clients.s3 import S3StorageClient

from auth import UserAuth
from database import DecimalDynamoDBWrapper

# 定数定義
AWS_REGION = 'ap-northeast-1'
PATTERN = re.compile(r'v\d+(?!.*\d[kK]$)')
PROVIDER = ""

TOKEN_PARAM_BY_PROVIDER = {
    "ai21": "maxTokens",
    "amazon": "maxTokenCount",
    "meta": "max_gen_len",
    "default": "max_tokens"
}

# 初期設定
storage_client = S3StorageClient(bucket="chainlit-storage-034362035978-ap-northeast-1")
data_layer = DynamoDBDataLayer(table_name="ChainlitData", storage_provider=storage_client)
wrapped_data_layer = DecimalDynamoDBWrapper(data_layer)
cl_data._data_layer = wrapped_data_layer.data_layer
# インスタンス化
user_auth = UserAuth(
    chainlit_table_name="ChainlitData",
    auth_table_name="UserAuth"
)

def encode_image_to_base64(image, image_format):
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return b64encode(buffer.getvalue()).decode("utf-8")


@cl.on_chat_start
async def main():
    cl.user_session.set("chat_history", [])

    bedrock = boto3.client("bedrock", region_name=AWS_REGION)
    response = bedrock.list_foundation_models(byOutputModality="TEXT")

    model_ids = [
        item['modelId']
        for item in response["modelSummaries"]
        if PATTERN.search(item['modelId'])
    ]

    settings = await cl.ChatSettings([
        Select(
            id="Model",
            label="Amazon Bedrock - Model",
            values=model_ids,
            initial_index=model_ids.index("anthropic.claude-3-haiku-20240307-v1:0"),
        ),
        Slider(
            id="Temperature",
            label="Temperature",
            initial=0.3,
            min=0,
            max=1,
            step=0.1,
        ),
        Slider(
            id="MAX_TOKEN_SIZE",
            label="Max Token Size",
            initial=1024,
            min=256,
            max=8192,
            step=256,
        ),
    ]).send()
    await setup_runnable(settings)

@cl.on_settings_update
async def setup_runnable(settings):
    bedrock_model_id = settings["Model"]

    llm = ChatBedrock(
        model_id=bedrock_model_id,
        model_kwargs={"temperature": settings["Temperature"]}
    )

    global PROVIDER
    PROVIDER = bedrock_model_id.split(".")[0]

    token_param = TOKEN_PARAM_BY_PROVIDER.get(
        PROVIDER, TOKEN_PARAM_BY_PROVIDER["default"]
    )

    llm.model_kwargs[token_param] = int(settings["MAX_TOKEN_SIZE"])

     # チャット履歴を含むプロンプトテンプレートを作成
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful chatbot. Consider the chat history for context when responding."),
        MessagesPlaceholder(variable_name="chat_history"),  # チャット履歴用のプレースホルダー
        ("human", "{input}")
    ])

    # チャット履歴を適切な形式に変換する関数
    def format_chat_history(chat_history):
        formatted_messages = []
        for message in chat_history:
            if message["role"] == "user":
                formatted_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                formatted_messages.append(("assistant", message["content"]))
        return formatted_messages

    # チャット履歴を含める処理を追加
    chain = RunnablePassthrough.assign(
        chat_history=lambda x: format_chat_history(cl.user_session.get("chat_history", []))
    ) | prompt | llm | StrOutputParser()

    cl.user_session.set("runnable", chain)

@cl.on_message
async def on_message(message: cl.Message):
    chat_history = cl.user_session.get("chat_history")
    runnable = cl.user_session.get("runnable")

    # runnableがNoneの場合のエラーハンドリング
    if runnable is None:
        # セッションの設定を取得
        settings = cl.user_session.get("settings")
        if settings:
            # runnableを再設定
            await setup_runnable(settings)
            runnable = cl.user_session.get("runnable")
        else:
            # エラーメッセージを表示
            error_msg = cl.Message(content="チャットの設定が見つかりません。ページを更新してください。")
            await error_msg.send()
            return

    # ユーザーメッセージを履歴に追加
    chat_history.append({"role": "user", "content": message.content})

    if PROVIDER == "anthropic":
        content = []
        for file in (message.elements or []):
            if file.path and "image" in file.mime:
                image = Image.open(file.path)
                bs64 = encode_image_to_base64(
                    image,
                    file.mime.split('/')[-1].upper()
                )
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": file.mime,
                        "data": bs64
                    }
                })
        content_text = {"type": "text", "text": message.content}
        content.append(content_text)
        input_data = {"input": content}
    else:
        input_data = {"input": message.content}

    res = cl.Message(content="", author=f'Chatbot: {PROVIDER.capitalize()}')

    async for chunk in runnable.astream(
        input_data,
        config=RunnableConfig(callbacks=[cl.LangchainCallbackHandler()]),
    ):
        await res.stream_token(chunk)

    await res.send()

    # アシスタントの応答を履歴に追加
    chat_history.append({"role": "assistant", "content": res.content})

# Decimal型を float に変換するヘルパー関数
def convert_decimal_to_float(obj):
    if isinstance(obj, dict):
        return {key: convert_decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal_to_float(item) for item in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

@cl.on_chat_resume
async def on_chat_resume(thread):
    cl.user_session.set("chat_history", [])
    
    # chat_settingsを直接取得
    chat_settings = cl.user_session.get("chat_settings")
    print(chat_settings)
    print("Chat settings on resume:", chat_settings)
    
    if chat_settings:
        # settingsとして保存し直す
        cl.user_session.set("settings", chat_settings)
        
        # runnableを再設定
        try:
            await setup_runnable(chat_settings)
            print("Runnable setup completed on resume")
        except Exception as e:
            print(f"Error setting up runnable: {e}")
    
    # チャット履歴の復元（元の方法）
    if thread.get("metadata"):
        if thread["metadata"].get("chat_history"):
            cl.user_session.set("chat_history", thread["metadata"]["chat_history"])

@cl.set_starters
async def set_starters():
    return [
        cl.Starter(
            label="Morning routine ideation",
            message="Can you help me create a personalized morning routine that would help increase my productivity throughout the day? Start by asking me about my current habits and what activities energize me in the morning.",
            icon="/public/idea.svg",
            ),

        cl.Starter(
            label="Explain superconductors",
            message="Explain superconductors like I'm five years old.",
            icon="/public/learn.svg",
            ),
        cl.Starter(
            label="Python script for daily email reports",
            message="Write a script to automate sending daily email reports in Python, and walk me through how I would set it up.",
            icon="/public/terminal.svg",
            ),
        cl.Starter(
            label="Text inviting friend to wedding",
            message="Write a text asking a friend to be my plus-one at a wedding next month. I want to keep it super short and casual, and offer an out.",
            icon="/public/write.svg",
            )
        ]

def encode_image_to_base64(image, image_format):
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return b64encode(buffer.getvalue()).decode("utf-8")


@cl.password_auth_callback
async def auth_callback(username: str, password: str):
    # 既存ユーザーの認証を試みる
    user = user_auth.verify_user(username, password)
    
    if user:
        return await cl_data.get_data_layer().get_user(username)
    else:
        return None