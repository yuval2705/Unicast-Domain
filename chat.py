from enum import Enum

class Request_Type(bytes, Enum):
    NEW_CONNECTION = b'\x00'
    MESSAGE = b'\x01'
    EXIT = b'\xFF'


class Chat_Request:
    MAX_REQUEST_SIZE = 1024
    SPLIT_MAGIC = b'\xcc\xdd'

    def __init__(self, packet_type:Request_Type, *args):
        self.type = packet_type
        self.args = args

    def encode(self) -> bytes:
        """
        Encodes this `Chat_Request` object so it can be sent as bytes over sockets.

        @param self: The `Chat_Request` object to be encoded.
        @return: A `bytes` object that represents the `Chat_Request` encoded value.
        """
        msg = self.type
        for v in self.args:
            msg += Chat_Request.SPLIT_MAGIC + v
        return msg
    
    def decode(raw_request:bytes) -> "Chat_Request":
        """
        Decoded a `bytes` object as a `Chat_Request` object.

        @param raw_request: A `bytes` object to be decoded as a `Chat_Request`
        @return: A `Chat_Request` object decoded from the given raw request.
        """
        message_parts = raw_request.split(Chat_Request.SPLIT_MAGIC)
        packet_type = message_parts[0]
        args = message_parts[1:]
        return Chat_Request(packet_type, *args)


class New_Connection_Request(Chat_Request):
    def __init__(self, username:str, room:str):
        super().__init__(Request_Type.NEW_CONNECTION, username.encode(), room.encode())


class Message_Request(Chat_Request):
    def __init__(self, raw_message:bytes):
        super().__init__(Request_Type.MESSAGE, raw_message)


class Close_Request(Chat_Request):
    def __init__(self, close_message:bytes=b""):
        super().__init__(Request_Type.EXIT, close_message)
