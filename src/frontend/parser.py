from typing import List
from src.frontend.lexer import TokenType, Token
from src.core.ast_nodes import (
    Assignment, BinaryExpr, CallExpr, ExprStmt, 
    Identifier, IndexAccess, MemberAccess, UnaryExpr, Literal, Stmt, Expr
)

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def consume(self, expected_type: TokenType = None) -> Token:
        token = self.tokens[self.pos]
        if expected_type and token.type != expected_type:
            raise SyntaxError(f"Expected {expected_type}, got {token.type} ('{token.value}') at line {token.line}")
        self.pos += 1
        return token

    def parse_stmts(self) -> List[Stmt]:
        stmts = []
        while self.peek().type != TokenType.EOF:
            if self.peek().type == TokenType.SEMI:
                self.consume()
                continue
                
            stmts.append(self.parse_stmt())
            
            if self.peek().type == TokenType.SEMI:
                self.consume()
                
        return stmts

    def parse_stmt(self) -> Stmt:
        left = self.parse_expr()
        
        if self.peek().value == "=":
            self.consume()
            right = self.parse_expr()
            return Assignment(left, right)
            
        return ExprStmt(left)

    def parse_expr(self) -> Expr:
        return self.parse_logic_or()

    def parse_logic_or(self) -> Expr:
        node = self.parse_logic_and()
        while self.peek().value == "||":
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_logic_and())
        return node

    def parse_logic_and(self) -> Expr:
        node = self.parse_equality()
        while self.peek().value == "&&":
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_equality())
        return node

    def parse_equality(self) -> Expr:
        node = self.parse_relational()
        while self.peek().value in ["==", "!="]:
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_relational())
        return node

    def parse_relational(self) -> Expr:
        node = self.parse_additive()
        while self.peek().value in [">", "<", ">=", "<="]:
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_additive())
        return node

    def parse_additive(self) -> Expr:
        node = self.parse_multiplicative()
        while self.peek().value in ["+", "-"]:
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_multiplicative())
        return node

    def parse_multiplicative(self) -> Expr:
        node = self.parse_unary()
        while self.peek().value in ["*", "/"]:
            op = self.consume().value
            node = BinaryExpr(node, op, self.parse_unary())
        return node

    def parse_unary(self) -> Expr:
        if self.peek().value == "!":
            op = self.consume().value
            return UnaryExpr(op, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> Expr:
        node = self.parse_primary()
        while True:
            if self.peek().type == TokenType.DOT:
                self.consume()
                member = self.consume(TokenType.ID).value
                node = MemberAccess(node, member)
                
            elif self.peek().type == TokenType.LPAREN:
                self.consume()
                args = []
                if self.peek().type != TokenType.RPAREN:
                    args.append(self.parse_expr())
                    while self.peek().type == TokenType.COMMA:
                        self.consume()
                        args.append(self.parse_expr())
                self.consume(TokenType.RPAREN)
                node = CallExpr(node, args)
                
            elif self.peek().type == TokenType.LBRACKET:
                self.consume()
                index = self.parse_expr()
                self.consume(TokenType.RBRACKET)
                node = IndexAccess(node, index)
                
            else:
                break
        return node

    def parse_primary(self) -> Expr:
        token = self.peek()
        
        if token.type == TokenType.NUM:
            self.consume()
            return Literal(token.value, 'int')
            
        if token.type == TokenType.ID:
            self.consume()
            if token.value in ["true", "false"]:
                return Literal(token.value, 'bool')
            return Identifier(token.value)
            
        if token.type == TokenType.LPAREN:
            self.consume()
            expr = self.parse_expr()
            self.consume(TokenType.RPAREN)
            return expr
            
        raise SyntaxError(f"Unexpected token '{token.value}' of type {token.type} at line {token.line}")