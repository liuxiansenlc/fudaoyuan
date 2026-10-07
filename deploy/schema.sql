-- ============================================================================
-- 奖学金材料审核工作台 · MySQL 建表脚本（含中文注释）
-- 数据库：fudao（可自行改名）
-- 字符集：utf8mb4 / utf8mb4_unicode_ci
-- 引擎：InnoDB
-- 用法：mysql -u root -p < schema.sql
-- 说明：本脚本与应用内 web/db.py 的 SCHEMA_MYSQL 一一对应，
--       生产环境可直接用本脚本建库，也可让应用启动时自动建表。
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `fudao`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
USE `fudao`;

-- ---------------------------------------------------------------------------
-- 1. users 用户表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
    `id`           INT PRIMARY KEY AUTO_INCREMENT            COMMENT '用户ID（自增主键）',
    `username`     VARCHAR(64) NOT NULL UNIQUE               COMMENT '登录用户名（唯一）',
    `display_name` VARCHAR(64) DEFAULT ''                    COMMENT '显示名称（中文姓名）',
    `pwd_hash`     VARCHAR(255) NOT NULL                     COMMENT '密码哈希（bcrypt，不存明文）',
    `is_admin`     TINYINT NOT NULL DEFAULT 0                COMMENT '是否系统管理员 0=审核员 1=管理员',
    `is_active`    TINYINT NOT NULL DEFAULT 1                COMMENT '账号是否启用 0=停用 1=正常',
    `created_at`   VARCHAR(20) NOT NULL                      COMMENT '创建时间（ISO8601）'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='系统用户（管理员/审核员）';

-- ---------------------------------------------------------------------------
-- 2. programs 奖学金项目表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `programs` (
    `id`              INT PRIMARY KEY AUTO_INCREMENT         COMMENT '项目ID（自增主键）',
    `name`            VARCHAR(128) NOT NULL                  COMMENT '奖学金项目名称（如：国家奖学金）',
    `category`        VARCHAR(32) DEFAULT '国家级'           COMMENT '奖学金类别（国家级/省级/市级/校级/院级/社会类/其他）',
    `description`     VARCHAR(512) DEFAULT ''                COMMENT '项目描述',
    `section_scheme`  TEXT                                   COMMENT '板块方案JSON（哪些板块、顺序、是否必需）',
    `rules_overrides` TEXT                                   COMMENT '规则覆盖JSON（只对本项目生效的阈值）',
    `template_path`   VARCHAR(512) DEFAULT ''                COMMENT '导出Word用的模板docx路径',
    `quota`           INT DEFAULT 0                          COMMENT '名额配额（0=不限）',
    `reject_mode`     VARCHAR(16) DEFAULT ''                 COMMENT '人工否定项导出处理方式 remove=剔除 mark=保留标注 空=跟随全局',
    `status`          VARCHAR(16) NOT NULL DEFAULT 'active'  COMMENT '状态 active=启用 archived=归档',
    `sort_order`      INT NOT NULL DEFAULT 100               COMMENT '排序权重（越小越靠前）',
    `created_by`      INT                                    COMMENT '创建人用户ID',
    `created_at`      VARCHAR(20) NOT NULL                   COMMENT '创建时间',
    `updated_at`      VARCHAR(20)                            COMMENT '最后更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='奖学金项目（一套申报口径）';

-- ---------------------------------------------------------------------------
-- 3. batches 批次表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `batches` (
    `id`          INT PRIMARY KEY AUTO_INCREMENT             COMMENT '批次ID（自增主键）',
    `program_id`  INT                                        COMMENT '所属奖学金项目ID（关联 programs.id）',
    `name`        VARCHAR(128) NOT NULL                      COMMENT '批次名称（如：2024国奖第一批）',
    `note`        VARCHAR(512) DEFAULT ''                    COMMENT '批次备注',
    `status`      VARCHAR(16) NOT NULL DEFAULT 'draft'       COMMENT '批次状态 draft=草稿 processing=处理中 done=完成',
    `created_by`  INT                                        COMMENT '创建人用户ID',
    `created_at`  VARCHAR(20) NOT NULL                       COMMENT '创建时间',
    `updated_at`  VARCHAR(20)                                COMMENT '最后更新时间',
    INDEX `idx_batches_prog` (`program_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='审核批次（一批材料对应一个奖学金项目）';

-- ---------------------------------------------------------------------------
-- 4. files 学生材料文件表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `files` (
    `id`           INT PRIMARY KEY AUTO_INCREMENT            COMMENT '文件ID（自增主键）',
    `batch_id`     INT NOT NULL                              COMMENT '所属批次ID（关联 batches.id）',
    `student_name` VARCHAR(64) DEFAULT ''                    COMMENT '学生姓名（从docx解析出）',
    `orig_name`    VARCHAR(255) NOT NULL                     COMMENT '原始文件名',
    `stored_path`  VARCHAR(512) NOT NULL                     COMMENT '服务器存储路径',
    `sha256`       VARCHAR(64) DEFAULT ''                    COMMENT '文件SHA256指纹',
    `size`         BIGINT DEFAULT 0                          COMMENT '文件大小（字节）',
    `n_items`      INT DEFAULT 0                             COMMENT '识别出的奖项条目数',
    `n_images`     INT DEFAULT 0                             COMMENT '识别出的图片数',
    `status`       VARCHAR(24) NOT NULL DEFAULT 'uploaded'   COMMENT '处理状态 uploaded=已上传 parsed=已解析 analyzed=已出结论 failed=失败',
    `error`        TEXT                                      COMMENT '失败原因',
    `analyzed_at`  VARCHAR(20)                               COMMENT '出结论时间',
    `created_at`   VARCHAR(20) NOT NULL                      COMMENT '上传时间',
    UNIQUE KEY `uk_batch_name` (`batch_id`, `orig_name`),
    INDEX `idx_files_batch` (`batch_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='学生申报材料文件';

-- ---------------------------------------------------------------------------
-- 5. refdata 参考核对数据表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `refdata` (
    `id`          INT PRIMARY KEY AUTO_INCREMENT             COMMENT '参考数据ID（自增主键）',
    `program_id`  INT                                        COMMENT '所属项目ID（NULL=全校通用）',
    `kind`        VARCHAR(24) NOT NULL                       COMMENT '类型 competition=A类竞赛表 ranking=成绩排名表 other=其他',
    `name`        VARCHAR(255) NOT NULL                      COMMENT '原始文件名',
    `stored_path` VARCHAR(512) NOT NULL                      COMMENT '服务器存储路径',
    `size`        BIGINT DEFAULT 0                           COMMENT '文件大小（字节）',
    `note`        VARCHAR(512) DEFAULT ''                    COMMENT '备注',
    `uploaded_by` INT                                        COMMENT '上传人用户ID',
    `created_at`  VARCHAR(20) NOT NULL                       COMMENT '上传时间',
    INDEX `idx_refdata_prog` (`program_id`, `kind`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='参考核对数据（A类竞赛表、成绩排名表等）';

-- ---------------------------------------------------------------------------
-- 6. rules_overrides 全局规则覆盖表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `rules_overrides` (
    `rule_key`   VARCHAR(96) PRIMARY KEY                     COMMENT '规则键（如 reading_rules.physical_pass）',
    `rule_value` VARCHAR(512) NOT NULL                       COMMENT '规则值（JSON）',
    `updated_by` INT                                         COMMENT '修改人用户ID',
    `updated_at` VARCHAR(20) NOT NULL                        COMMENT '修改时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='全局规则覆盖（所有奖学金生效）';

-- ---------------------------------------------------------------------------
-- 7. program_rules 项目规则覆盖表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `program_rules` (
    `program_id` INT NOT NULL                                COMMENT '所属项目ID（关联 programs.id）',
    `rule_key`   VARCHAR(96) NOT NULL                        COMMENT '规则键（如 reading_rules.physical_pass）',
    `rule_value` VARCHAR(512) NOT NULL                       COMMENT '规则值（JSON）',
    `updated_by` INT                                         COMMENT '修改人用户ID',
    `updated_at` VARCHAR(20) NOT NULL                        COMMENT '修改时间',
    PRIMARY KEY (`program_id`, `rule_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='项目级规则覆盖（只对特定奖学金生效，优先于全局）';

-- ---------------------------------------------------------------------------
-- 8. rule_nl 自然语言规则表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `rule_nl` (
    `id`           INT PRIMARY KEY AUTO_INCREMENT            COMMENT '规则ID（自增主键）',
    `program_id`   INT                                       COMMENT '所属项目ID（NULL=全局规则）',
    `nl_text`      TEXT NOT NULL                             COMMENT '自然语言规则原文（中文）',
    `dsl`          TEXT                                      COMMENT '编译后的受限DSL（JSON）',
    `status`       VARCHAR(16) NOT NULL DEFAULT 'draft'      COMMENT '状态 draft=草稿 enabled=启用 disabled=停用',
    `priority`     INT NOT NULL DEFAULT 100                  COMMENT '优先级（越大越先执行）',
    `compile_note` TEXT                                      COMMENT '编译说明/校验信息',
    `created_by`   INT                                       COMMENT '创建人用户ID',
    `created_at`   VARCHAR(20) NOT NULL                      COMMENT '创建时间',
    `updated_at`   VARCHAR(20)                               COMMENT '最后更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='自然语言自定义规则';

-- ---------------------------------------------------------------------------
-- 9. model_configs 大模型配置表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `model_configs` (
    `id`          INT PRIMARY KEY AUTO_INCREMENT             COMMENT '配置ID（自增主键）',
    `name`        VARCHAR(64) NOT NULL                       COMMENT '配置名称（如：DeepSeek主模型）',
    `base_url`    VARCHAR(255) NOT NULL                      COMMENT 'API基地址（OpenAI兼容）',
    `model`       VARCHAR(128) NOT NULL                      COMMENT '模型名（如：deepseek-vl2）',
    `api_key_enc` BLOB                                       COMMENT 'API密钥（Fernet加密存储，不存明文）',
    `is_active`   TINYINT NOT NULL DEFAULT 0                 COMMENT '是否启用 0=否 1=是',
    `extra`       TEXT                                       COMMENT '扩展配置（JSON，如json_mode）',
    `created_by`  INT                                        COMMENT '创建人用户ID',
    `created_at`  VARCHAR(20) NOT NULL                       COMMENT '创建时间',
    `updated_at`  VARCHAR(20)                                COMMENT '最后更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='大模型API配置（识图用）';

-- ---------------------------------------------------------------------------
-- 10. tasks 任务队列表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tasks` (
    `id`             INT PRIMARY KEY AUTO_INCREMENT          COMMENT '任务ID（自增主键）',
    `kind`           VARCHAR(24) NOT NULL                    COMMENT '任务类型 read_images=读图 analyze=出结论 export=导出',
    `batch_id`       INT                                     COMMENT '所属批次ID',
    `file_id`        INT                                     COMMENT '所属文件ID（单文件任务时用）',
    `payload`        TEXT                                    COMMENT '任务参数（JSON）',
    `status`         VARCHAR(16) NOT NULL DEFAULT 'queued'   COMMENT '状态 queued=排队 running=执行中 done=完成 failed=失败 canceled=取消',
    `phase`          VARCHAR(64) DEFAULT ''                  COMMENT '当前阶段（如：读图中）',
    `progress_done`  INT NOT NULL DEFAULT 0                  COMMENT '已完成数量',
    `progress_total` INT NOT NULL DEFAULT 0                  COMMENT '总数量',
    `message`        VARCHAR(512) DEFAULT ''                 COMMENT '进度消息',
    `error`          TEXT                                    COMMENT '错误信息',
    `created_by`     INT                                     COMMENT '创建人用户ID',
    `created_at`     VARCHAR(20) NOT NULL                    COMMENT '创建时间',
    `started_at`     VARCHAR(20)                             COMMENT '开始执行时间',
    `finished_at`    VARCHAR(20)                             COMMENT '完成时间',
    `claimed_by`     VARCHAR(96) DEFAULT ''                  COMMENT '认领该任务的worker标识',
    INDEX `idx_tasks_status` (`status`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='后台任务队列（读图/出结论/导出）';

-- ---------------------------------------------------------------------------
-- 11. match_states 人工审核判定表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `match_states` (
    `file_id`    INT NOT NULL                                COMMENT '文件ID（关联 files.id）',
    `item_id`    INT NOT NULL                                COMMENT '奖项条目ID',
    `decision`   VARCHAR(16) NOT NULL                        COMMENT '人工判定 confirm=确认 reject=否定 reassign=改派',
    `img_sha`    VARCHAR(64) DEFAULT ''                      COMMENT '改派后的图片SHA256',
    `img_file`   VARCHAR(128) DEFAULT ''                     COMMENT '改派后的图片文件名',
    `note`       VARCHAR(512) DEFAULT ''                     COMMENT '人工备注',
    `is_manual`  TINYINT NOT NULL DEFAULT 1                  COMMENT '是否人工判定 1=是',
    `updated_by` INT                                         COMMENT '操作人用户ID',
    `updated_at` VARCHAR(20) NOT NULL                        COMMENT '操作时间',
    PRIMARY KEY (`file_id`, `item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='人工审核判定（覆盖引擎自动结论）';

-- ---------------------------------------------------------------------------
-- 12. orphan_adoptions 未认领图片采纳表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `orphan_adoptions` (
    `file_id`    INT NOT NULL                                COMMENT '文件ID（关联 files.id）',
    `img_sha`    VARCHAR(64) NOT NULL                        COMMENT '被采纳图片的SHA256',
    `award_name` VARCHAR(255) NOT NULL                       COMMENT '人工填写的奖项名称',
    `section`    VARCHAR(64) DEFAULT ''                      COMMENT '归入的板块（如：技能）',
    `adopted_by` INT                                         COMMENT '采纳人用户ID',
    `created_at` VARCHAR(20) NOT NULL                        COMMENT '采纳时间',
    PRIMARY KEY (`file_id`, `img_sha`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='未认领图片的人工采纳记录';

-- ---------------------------------------------------------------------------
-- 13. api_usage 模型用量统计表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `api_usage` (
    `day`    VARCHAR(12) NOT NULL                            COMMENT '统计日期（YYYY-MM-DD）',
    `model`  VARCHAR(128) NOT NULL DEFAULT ''                COMMENT '模型名',
    `calls`  INT NOT NULL DEFAULT 0                          COMMENT '调用次数',
    `images` INT NOT NULL DEFAULT 0                          COMMENT '识别的图片张数',
    PRIMARY KEY (`day`, `model`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='大模型每日用量统计（成本控制）';

-- ---------------------------------------------------------------------------
-- 14. audit_log 操作审计日志表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `audit_log` (
    `id`     INT PRIMARY KEY AUTO_INCREMENT                  COMMENT '日志ID（自增主键）',
    `actor`  INT                                             COMMENT '操作人用户ID',
    `action` VARCHAR(64) NOT NULL                            COMMENT '操作类型（如 login/review.reject）',
    `target` VARCHAR(128) DEFAULT ''                         COMMENT '操作对象（如文件ID）',
    `detail` VARCHAR(512) DEFAULT ''                         COMMENT '操作详情',
    `at`     VARCHAR(20) NOT NULL                            COMMENT '操作时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='操作审计日志（留痕）';

-- ---------------------------------------------------------------------------
-- 15. app_settings 应用运行期设置表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `app_settings` (
    `k`          VARCHAR(64) PRIMARY KEY                     COMMENT '设置键（如 mask_sensitive）',
    `v`          VARCHAR(512) DEFAULT ''                     COMMENT '设置值',
    `updated_by` INT                                         COMMENT '修改人用户ID',
    `updated_at` VARCHAR(20)                                 COMMENT '修改时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='应用运行期设置（可界面切换的开关）';
