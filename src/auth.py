import hashlib
import boto3
import chainlit as cl
import os
from dotenv import load_dotenv
import datetime
import uuid

# .envファイルから環境変数を読み込み
load_dotenv()

class UserAuth:
    def __init__(self, chainlit_table_name, auth_table_name):
        self.dynamodb = boto3.resource('dynamodb')
        self.chainlit_table = self.dynamodb.Table(chainlit_table_name)
        self.auth_table = self.dynamodb.Table(auth_table_name)
        self._ensure_admin_exists()
    
    def hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _ensure_admin_exists(self):
        """管理者ユーザーが存在しない場合は作成する"""
        try:
            admin_username = os.getenv('ADMIN_USERNAME')
            admin_password = os.getenv('ADMIN_PASSWORD')
            
            if not admin_username or not admin_password:
                print("Warning: Admin credentials not found in environment variables")
                return
            
            # 管理者ユーザーの存在確認
            admin_auth = self.get_user_auth(admin_username)
            
            if not admin_auth:
                # 管理者ユーザーが存在しない場合は作成
                try:
                    self.create_user(admin_username, admin_password, role="admin")
                    print(f"Admin user '{admin_username}' created successfully")
                except Exception as e:
                    print(f"Error creating admin user: {str(e)}")
            else:
                print(f"Admin user '{admin_username}' already exists")
                
        except Exception as e:
            print(f"Error in _ensure_admin_exists: {str(e)}")
    
    def get_user_auth(self, username: str):
        """認証情報の取得"""
        response = self.auth_table.get_item(
            Key={
                'username': username
            }
        )
        return response.get('Item')
    
    def create_user(self, username: str, password: str, role: str = "user"):
        """ユーザーを作成する"""
        if role != "admin":
            raise PermissionError("Only admin users can be created")
            
        hashed_password = self.hash_password(password)
        
        # 認証情報をAuthテーブルに保存
        auth_item = {
            'username': username,
            'password': hashed_password,
            'role': role,
            'createdAt': datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        self.auth_table.put_item(Item=auth_item)

        # ChainlitDataテーブルへの書き込み
        self.chainlit_table.put_item(
            Item={
                'PK': f"USER#{username}",
                'SK': "USER",
                'id': str(uuid.uuid4()),
                'identifier': username,
                'createdAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'metadata': {}
            }
        )

    
    def verify_user(self, username: str, password: str):
        """ユーザー認証"""
        user_auth = self.get_user_auth(username)
        if not user_auth:
            return None
        
        hashed_password = self.hash_password(password)

        if user_auth['password'] == hashed_password:
            print("test")
            return cl.User(
                identifier=username,
                metadata={"role": user_auth['role'], "provider": "credentials"}
            )
        return None
