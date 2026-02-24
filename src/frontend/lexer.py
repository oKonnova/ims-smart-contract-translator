import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List

class TokenType(Enum):
    ID = auto()
    NUM = auto()
    OP = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    DOT = auto()
    COMMA = auto()
    SEMI = auto()  
    EOF = auto()

@dataclass
class Token:
    type: TokenType
    value: str
    line: int = 1

class Lexer:
    TOKEN_SPECS = [
        ('NUM',       r'\d+'),
        ('OP',        r'==|!=|>=|<=|&&|\|\||[><!+\-*/=]'),
        ('LPAREN',    r'\('),
        ('RPAREN',    r'\)'),
        ('LBRACKET',  r'\['),
        ('RBRACKET',  r'\]'),
        ('DOT',       r'\.'),
        ('COMMA',     r','),
        ('SEMI',      r';'),  
        ('ID',        r'[a-zA-Z_][a-zA-Z0-9_]*'),
        ('SKIP',      r'[ \t\r\n]+'), 
        ('MISMATCH',  r'.')
    ]
    
    _REGEX = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in TOKEN_SPECS))

    def tokenize(self, code: str) -> List[Token]:
        tokens = []
        line = 1
        
        for mo in self._REGEX.finditer(code):
            kind = mo.lastgroup
            value = mo.group()
            
            if kind == 'SKIP':
                line += value.count('\n')
                continue
            
            if kind == 'MISMATCH':
                raise SyntaxError(f"Unexpected character '{value}' at line {line}")
                
            tokens.append(Token(TokenType[kind], value, line))
            
        tokens.append(Token(TokenType.EOF, "", line))
        return tokens