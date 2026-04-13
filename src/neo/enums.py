from enum import StrEnum


class NodeType(StrEnum):
    FINDING = "finding"
    CONCEPT = "concept"
    THEORY = "theory"
    QUESTION = "question"
    IDEA = "idea"
    ANSWER = "answer"
    SYNTHESIS = "synthesis"


class EdgeType(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    PREREQUISITE_FOR = "prerequisite_for"
    EXTENDS = "extends"
    EXAMPLE_OF = "example_of"
    QUESTIONS = "questions"
    RESOLVES = "resolves"
    INSPIRED = "inspired"
    CONNECTS = "connects"


class SparkType(StrEnum):
    OPEN_QUESTION = "open_question"
    CONTRADICTION = "contradiction"
    WEAK_EDGE = "weak_edge"
    ISOLATED_NODE = "isolated_node"
    THIN_DOMAIN = "thin_domain"


class SparkStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class SourceType(StrEnum):
    URL = "url"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    RESEARCH_SESSION = "research_session"
    MANUAL = "manual"
