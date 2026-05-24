-- ============================================================
-- AgentPaperDistiller V2.0 企业级数据库建表语句
-- MySQL 8.0+, InnoDB, utf8mb4
-- ============================================================

CREATE DATABASE IF NOT EXISTS AgentPaperDistriller
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE AgentPaperDistriller;

-- ----------------------------------------------------------
-- 1. 用户表
-- ----------------------------------------------------------
CREATE TABLE users (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL,
    username        VARCHAR(100)    NOT NULL,
    hashed_password VARCHAR(255)    NOT NULL,
    role            ENUM('user','admin') NOT NULL DEFAULT 'user',
    is_active       TINYINT(1)      NOT NULL DEFAULT 1,
    email_verified  TINYINT(1)      NOT NULL DEFAULT 0,
    email_verify_token VARCHAR(128) DEFAULT NULL,          -- 邮箱验证 token
    password_reset_token VARCHAR(128) DEFAULT NULL,        -- 密码重置 token
    password_reset_expires DATETIME DEFAULT NULL,          -- 重置 token 过期时间
    last_login_at   DATETIME        DEFAULT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_email (email),
    INDEX idx_role (role),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 2. 论文表（用户私有，元数据存 MySQL，文件存磁盘）
-- ----------------------------------------------------------
CREATE TABLE papers (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    paper_id        VARCHAR(100)    NOT NULL,              -- 业务 ID，如 "1.Attention_Is_All_You_Need"
    title           VARCHAR(500)    NOT NULL,
    source_filename VARCHAR(500)    DEFAULT NULL,
    status          ENUM('processing','completed','failed') NOT NULL DEFAULT 'processing',
    target_language VARCHAR(50)     NOT NULL DEFAULT 'Chinese',
    summary_template VARCHAR(200)   NOT NULL DEFAULT 'tinghua.md',
    year            INT             DEFAULT NULL,
    authors         JSON            DEFAULT NULL,           -- ["Author 1", "Author 2"]
    domain_tags     JSON            DEFAULT NULL,           -- ["Backdoor Attack", "CV"]

    -- 文件路径（磁盘存储）
    pdf_path        VARCHAR(1000)   DEFAULT NULL,          -- PDF 文件磁盘路径
    output_dir      VARCHAR(1000)   DEFAULT NULL,          -- 处理结果目录路径

    -- 所有权
    user_id         BIGINT UNSIGNED NOT NULL,
    is_public       TINYINT(1)      NOT NULL DEFAULT 0,     -- 0=私有, 1=公开（向量化贡献到共享池）

    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_paper_id (paper_id),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_is_public (is_public),
    INDEX idx_created_at (created_at DESC),
    CONSTRAINT fk_papers_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 3. 模板表
-- ----------------------------------------------------------
CREATE TABLE templates (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    content         LONGTEXT        NOT NULL,
    domain_tag      VARCHAR(100)    DEFAULT 'General',
    is_default      TINYINT(1)      NOT NULL DEFAULT 0,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 4. 任务记录表（替代内存 TaskBroker 的持久化层）
-- ----------------------------------------------------------
CREATE TABLE task_records (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(100)    NOT NULL,
    paper_id        VARCHAR(100)    NOT NULL,
    status          VARCHAR(50)     NOT NULL DEFAULT 'queued',
    progress        INT             NOT NULL DEFAULT 0,
    message         TEXT            DEFAULT NULL,
    generation_model VARCHAR(100)   DEFAULT NULL,
    evaluation_model VARCHAR(100)   DEFAULT NULL,
    collaboration_mode VARCHAR(200) DEFAULT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_task_id (task_id),
    INDEX idx_paper_id (paper_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at DESC)
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 5. 用户 API 配置表（AES 加密存储）
-- ----------------------------------------------------------
CREATE TABLE user_api_configs (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id             BIGINT UNSIGNED NOT NULL,
    deepseek_api_key    VARCHAR(500) DEFAULT NULL,          -- AES 加密
    deepseek_base_url   VARCHAR(500) DEFAULT NULL,
    qwen_api_key        VARCHAR(500) DEFAULT NULL,          -- AES 加密
    qwen_base_url       VARCHAR(500) DEFAULT NULL,
    tavily_api_key      VARCHAR(500) DEFAULT NULL,          -- AES 加密
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_user_id (user_id),
    CONSTRAINT fk_api_config_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 6. 系统配置表（管理员可改）
-- ----------------------------------------------------------
CREATE TABLE system_settings (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    setting_key     VARCHAR(100)    NOT NULL,
    setting_value   TEXT            DEFAULT NULL,
    setting_type    ENUM('string','int','float','bool','json') NOT NULL DEFAULT 'string',
    description     VARCHAR(500)    DEFAULT NULL,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_setting_key (setting_key)
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 7. 对话会话表
-- ----------------------------------------------------------
CREATE TABLE chat_sessions (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(100)    NOT NULL,
    user_id         BIGINT UNSIGNED NOT NULL,
    paper_id        VARCHAR(100)    NOT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_session_id (session_id),
    INDEX idx_user_paper (user_id, paper_id),
    CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 8. 对话消息表
-- ----------------------------------------------------------
CREATE TABLE chat_messages (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(100)    NOT NULL,
    role            ENUM('user','assistant','system') NOT NULL,
    content         LONGTEXT        NOT NULL,
    thinking_chain  JSON            DEFAULT NULL,           -- ReAct 思考链
    contexts        JSON            DEFAULT NULL,           -- RAG 检索上下文摘要
    deep_search     TINYINT(1)      NOT NULL DEFAULT 0,     -- 是否深度搜索
    token_usage     JSON            DEFAULT NULL,           -- {"prompt": N, "completion": N, "model": "..."}
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_session_id (session_id),
    INDEX idx_created_at (created_at),
    CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 9. 审计日志表
-- ----------------------------------------------------------
CREATE TABLE audit_logs (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id         BIGINT UNSIGNED DEFAULT NULL,
    action          VARCHAR(100)    NOT NULL,               -- 'login','upload','delete_paper','config_change'...
    resource_type   VARCHAR(50)     DEFAULT NULL,           -- 'paper','user','config','template'
    resource_id     VARCHAR(200)    DEFAULT NULL,
    detail          JSON            DEFAULT NULL,
    ip_address      VARCHAR(45)     DEFAULT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_user_id (user_id),
    INDEX idx_action (action),
    INDEX idx_resource (resource_type, resource_id),
    INDEX idx_created_at (created_at DESC)
) ENGINE=InnoDB;

-- ----------------------------------------------------------
-- 10. 邮箱验证记录表
-- ----------------------------------------------------------
CREATE TABLE email_verifications (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL,
    token           VARCHAR(128)    NOT NULL,
    action          ENUM('register','reset_password') NOT NULL DEFAULT 'register',
    expires_at      DATETIME        NOT NULL,
    used            TINYINT(1)      NOT NULL DEFAULT 0,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_email_token (email, token),
    INDEX idx_expires (expires_at)
) ENGINE=InnoDB;

-- ============================================================
-- 初始数据
-- ============================================================

-- 系统配置默认值
INSERT INTO system_settings (setting_key, setting_value, setting_type, description) VALUES
('app_version',       'V2.0',                              'string', '应用版本号'),
('app_update_date',   '2026-05-24',                        'string', '版本更新日期'),
('app_author',        'ByteTitan-Star',                    'string', '应用作者'),
('app_changelog',     '引入 Harness 框架、ReAct Deep Search、用户系统、MySQL 持久化', 'string', '更新说明'),
('deepseek_model',    'deepseek-chat',                     'string', 'DeepSeek 默认模型'),
('qwen_model',        'qwen3-30b-a3b-instruct-2507',      'string', 'Qwen 默认模型'),
('max_chunk_chars',   '900',                               'int',    'PDF 切块最大字符数'),
('react_max_rounds',  '5',                                 'int',    'ReAct 深度搜索最大轮次'),
('react_enable_clarification', 'true',                     'bool',   '是否启用复杂问题澄清'),
('default_template',  'tinghua.md',                        'string', '默认摘要模板'),
('vector_shared',     'true',                              'bool',   '向量化数据是否全平台共享'),
('register_requires_email_verify', 'true',                 'bool',   '注册是否需要邮箱验证');
