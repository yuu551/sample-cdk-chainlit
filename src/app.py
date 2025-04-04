import re
import boto3
import chainlit as cl
from chainlit.input_widget import Select, Slider
from langchain_aws import ChatBedrock
from langchain.schema.runnable import RunnableConfig
from langchain.schema import StrOutputParser
from langchain.prompts import ChatPromptTemplate
from langchain.memory import ConversationBufferMemory
from langchain.schema import HumanMessage, AIMessage

import chainlit.data as cl_data
from chainlit.data.dynamodb import DynamoDBDataLayer
from chainlit.data.storage_clients.s3 import S3StorageClient
from database import DecimalDynamoDBWrapper
import chainlit as cl
from auth import UserAuth

PATTERN = re.compile(r'v\d+(?!.*\d[kK]$)')

DEFAULT_SYSTEM_PROMPT = """"""
storage_client = S3StorageClient(bucket="chainlit-storage-034362035978-ap-northeast-1")
data_layer = DynamoDBDataLayer(table_name="ChainlitData", storage_provider=storage_client)
wrapped_data_layer = DecimalDynamoDBWrapper(data_layer)
cl_data._data_layer = wrapped_data_layer.data_layer

# 認証インスタンスの初期化
user_auth = UserAuth(
    chainlit_table_name="ChainlitData",
    auth_table_name="UserAuth"
)

@cl.on_chat_start
async def start():
    try:
        # 利用可能なモデルの取得
        bedrock = boto3.client("bedrock", region_name="ap-northeast-1")
        response = bedrock.list_foundation_models(byOutputModality="TEXT")
        
        model_ids = [
            item['modelId']
            for item in response["modelSummaries"]
            if PATTERN.search(item['modelId'])
        ]

        # チャット設定の追加
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
                initial=0.7,
                min=0,
                max=1,
                step=0.1,
            )
        ]).send()
        
        # 会話履歴の初期化
        cl.user_session.set("message_history", [])
        
        await setup_chain(settings)
        
        await cl.Message(
            content="モデルを選択して、チャットを開始してください。"
        ).send()
        
    except Exception as e:
        await cl.Message(
            content=f"設定の初期化に失敗しました: {str(e)}"
        ).send()

@cl.on_settings_update
async def setup_chain(settings):
    try:
        llm = ChatBedrock(
            model_id=settings["Model"],
            model_kwargs={"temperature": settings["Temperature"]}
        )

        # プロンプトテンプレートの作成（会話履歴を含める）
        prompt = ChatPromptTemplate.from_messages([
            ("system", DEFAULT_SYSTEM_PROMPT),
            ("human", "これまでの会話内容:\n{chat_history}\n\n現在の質問:\n{input}")
        ])

        # チェーンの作成
        chain = prompt | llm | StrOutputParser()
        cl.user_session.set("chain", chain)
    except Exception as e:
        await cl.Message(
            content=f"モデルの設定に失敗しました: {str(e)}"
        ).send()

@cl.on_message
async def main(message: cl.Message):
    chain = cl.user_session.get("chain")
    message_history = cl.user_session.get("message_history")
    response = cl.Message(content="")
    
    try:
        chat_history = "\n".join([
            f"Human: {msg['human']}\nAssistant: {msg['ai']}"
            for msg in message_history
        ])

        async for chunk in chain.astream(
            {
                "input": message.content,
                "chat_history": chat_history
            },
            config=RunnableConfig(callbacks=[cl.LangchainCallbackHandler()]),
        ):
            await response.stream_token(chunk)
        
        # 会話履歴に追加
        message_history.append({
            "human": message.content,
            "ai": response.content
        })
        cl.user_session.set("message_history", message_history)
        
        await response.send()
        
    except Exception as e:
        await cl.Message(
            content=f"エラーが発生しました: {str(e)}"
        ).send()

@cl.password_auth_callback
async def auth_callback(username: str, password: str):
    """
    Chainlitの認証コールバック
    
    Args:
        username (str): ユーザー名
        password (str): パスワード
    
    Returns:
        User | None: 認証成功時はユーザーオブジェクト、失敗時はNone
    """
    user = user_auth.verify_user(username, password)
    
    if user:
        test = await cl_data.get_data_layer().get_user(username)
        return test
    return None

@cl.on_chat_resume
async def on_chat_resume(thread):
    # 会話履歴の初期化
    message_history = []
    
    # スレッドのメタデータから会話履歴を復元
    if thread.get("metadata") and thread["metadata"].get("message_history"):
        message_history = thread["metadata"]["message_history"]
    print(message_history)
    
    # セッションに会話履歴を設定
    cl.user_session.set("message_history", message_history)
    
    # チャット設定を復元
    settings = cl.user_session.get("chat_settings")
    print(settings)
    if settings:
        await setup_chain(settings)