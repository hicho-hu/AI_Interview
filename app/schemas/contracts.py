from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(BaseModel):
    code: str = "OK"
    message: str = "success"
    data: Any = None
    request_id: str


class Login(DTO):
    code: Text = Field(max_length=256)
    nickname: str = Field("求职者", max_length=80)


class DevLogin(DTO):
    identity: Text = Field(max_length=100)
    nickname: str = Field("开发用户", max_length=80)


class UserPatch(DTO):
    nickname: Text = Field(max_length=80)


class ConsentInput(DTO):
    consent_type: Literal["PRIVACY", "TERMS", "AI_PROCESSING", "MICROPHONE", "CAMERA"]
    policy_version: Text = Field(max_length=30)


class JobInput(DTO):
    job_title: Text = Field(max_length=120)
    jd_text: Text = Field(min_length=10, max_length=30000)
    seniority: Literal["JUNIOR", "MIDDLE", "SENIOR", "EXPERT"] = "MIDDLE"


class InterviewConfig(DTO):
    mode: Literal["TEXT", "VOICE", "VIDEO"] = "VOICE"
    duration_minutes: Literal[15, 30, 45] = 15
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "MEDIUM"
    question_count: int = Field(8, ge=1, le=20)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    allow_follow_up: bool = True
    max_follow_ups: int = Field(2, ge=0, le=3)
    max_clarifications: int = Field(2, ge=0, le=3)
    interview_type: Literal["COMPREHENSIVE", "TECHNICAL", "BEHAVIORAL"] = "COMPREHENSIVE"


class InterviewInput(DTO):
    job_profile_id: str
    resume_asset_id: Optional[str] = None
    config: InterviewConfig = Field(default_factory=lambda: InterviewConfig.model_validate({}))


class UploadInput(DTO):
    filename: Text = Field(max_length=200)
    content_type: Text = Field(max_length=120)
    size: int = Field(ge=1, le=32 * 1024 * 1024)
    media_type: Literal["AUDIO", "VIDEO", "RESUME", "JD_IMAGE"] = "AUDIO"
    interview_id: Optional[str] = None
    turn_id: Optional[str] = None
    duration_ms: Optional[int] = Field(None, ge=1, le=180000)


class UploadComplete(DTO):
    etag: Optional[str] = Field(None, max_length=128)
    checksum: Optional[str] = Field(None, pattern="^[a-fA-F0-9]{64}$")


class Answer(DTO):
    text: Optional[Text] = Field(None, max_length=20000)
    asset_id: Optional[str] = None
    asset_ids: Optional[list[str]] = Field(None, min_length=1, max_length=3)
    client_duration_ms: Optional[int] = Field(None, ge=1, le=180000)

    @model_validator(mode="after")
    def one_source(self):
        if sum(bool(v) for v in (self.text, self.asset_id, self.asset_ids)) != 1:
            raise ValueError("text、asset_id、asset_ids 必须且只能提供一个")
        if self.asset_ids and len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("asset_ids 不允许重复")
        return self


class Finish(DTO):
    reason: Literal["USER", "TIME_LIMIT", "QUESTION_LIMIT"] = "USER"


class OrderInput(DTO):
    product_code: Literal["SINGLE", "PACK_10", "MONTHLY"]


class FeedbackInput(DTO):
    interview_id: Optional[str] = None
    category: Literal["CONTENT", "REPORT", "PRIVACY", "OTHER"] = "OTHER"
    content: Text = Field(max_length=3000)


class FeedbackReply(DTO):
    status: Literal["IN_REVIEW", "RESOLVED", "REJECTED"]
    reply: Text = Field(max_length=3000)


class JobStructure(DTO):
    responsibilities: list[str] = Field(min_length=1, max_length=20)
    required_skills: list[str] = Field(min_length=1, max_length=20)
    preferred_skills: list[str] = Field(default_factory=list, max_length=20)
    experience_requirements: list[str] = Field(default_factory=list, max_length=20)
    competencies: list[str] = Field(min_length=1, max_length=10)


class ResumeStructure(DTO):
    summary: str
    education: list[str] = Field(default_factory=list)
    work_experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Question(DTO):
    text: Text = Field(max_length=2000)
    competency: Text = Field(max_length=120)
    question_type: str = Field(max_length=50)
    jd_evidence: Text = Field(max_length=1000)


class Plan(DTO):
    questions: list[Question] = Field(min_length=1, max_length=20)
    resume_job_matches: list[str] = Field(default_factory=list, max_length=20)


class Decision(DTO):
    action: Literal["FOLLOW_UP", "NEXT_QUESTION", "CLARIFY", "FINISH"]
    question: Optional[str] = Field(None, max_length=2000)
    reason: str = Field(max_length=1000)
    answer_summary: str = Field(max_length=2000)


class Dimension(DTO):
    name: Literal["岗位相关性", "结构逻辑", "专业深度", "案例证据", "问题解决", "表达清晰度"]
    score: Optional[float] = Field(None, ge=0, le=100)
    feedback: str
    evidence: list[str] = Field(default_factory=list, max_length=10)


class Review(DTO):
    turn_id: str
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]
    reference_approach: str
    evidence: list[str]


class ReportContent(DTO):
    overall_score: Optional[float] = Field(None, ge=0, le=100)
    dimensions: list[Dimension] = Field(min_length=6, max_length=6)
    reviews: list[Review]
    strengths: list[str]
    gaps: list[str]
    recommendations: list[str] = Field(min_length=1, max_length=10)
    practice_questions: list[str] = Field(min_length=1, max_length=10)
    job_match_feedback: str
    disclaimer: str = "评分仅为训练参考，不代表招聘方评价或录用概率。"


class Transcript(DTO):
    text: str = Field(max_length=20000)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    language: str
    duration_ms: int = Field(ge=1, le=180000)


class SynthesizedAudio(DTO):
    audio_base64: str = Field(max_length=14 * 1024 * 1024)
    mime_type: Literal["audio/mpeg"]
