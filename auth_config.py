from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

class AuthProfile(BaseModel):
    username: str
    password: str

class AuthConfig(BaseSettings):
    profiles: dict[str, AuthProfile] = Field(alias="auth")

    model_config = SettingsConfigDict(env_nested_delimiter="_")
