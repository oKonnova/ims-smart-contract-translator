from typing import List, Any
from dataclasses import dataclass

# --- AST Nodes ---
class ASTNode: pass
class Expr(ASTNode): pass

@dataclass
class Literal(Expr): value: Any; type_name: str

@dataclass
class Identifier(Expr): name: str

@dataclass
class MemberAccess(Expr): object: Expr; member: str

@dataclass
class CallExpr(Expr): target: Expr; args: List[Expr]

@dataclass
class IndexAccess(Expr): target: Expr; index: Expr

@dataclass
class BinaryExpr(Expr): left: Expr; op: str; right: Expr

@dataclass
class UnaryExpr(Expr): op: str; expr: Expr

class Stmt(ASTNode): pass

@dataclass
class Assignment(Stmt): target: Expr; value: Expr

@dataclass
class ExprStmt(Stmt): expr: Expr