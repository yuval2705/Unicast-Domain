import socket
from typing import List
import select
from queue import Queue
from chat import Request_Type, Chat_Request, Message_Request, Close_Request
import argparse

DEFAULT_SERVER_ADDR = "0.0.0.0"
DEFAULT_SERVER_PORT = 33333


class User_Session:
    """
    Holds additional information about the user session so that the server would know
    what each socket needs to get and to where he needs to send.
    """
    def __init__(self, username:str = None, room_name:str = None):
        self.username = username
        self.room_name = room_name
        self.new_messages = Queue()


class Room:
    def __init__(self, name:str, members:List[str] = list(), messages:List[bytes] = list()):
        self.name = name
        self._members = members
        self.messages = messages
        self.subscribers = list()

    def add_subscriber(self, user_session:User_Session) -> bool:
        """
        Adds a `User_Session` as a subscriber to be invoked when a new message is added.
        Returns indication if the operation was successful.

        @param user_session: The `User_Session` to subscribe.
        @return: A bool indication if the operation was succesful or not.
        """
        if user_session.username not in self._members:
            return False

        self.subscribers.append(user_session.new_messages)
        for msg in self.messages:
            user_session.new_messages.put(msg)
        return True

    def invoke_new_message(self, raw_message:bytes) -> None:
        """
        Add the new message to all of the subscribers.
        
        @param raw_message: The message to add.
        """
        self.messages.append(raw_message)
        for q in self.subscribers:
            try:
                q.put(raw_message)
            except Exception as e:
                self.subscribers.remove(q)

    def unsubscribe(self, user_session:User_Session) -> None:
        """
        Removes the given `User_Session` from the subscribers.

        @param user_session: The `User_Session` to remove from the subscribers.
        """
        if user_session.new_messages in self.subscribers:
            self.subscribers.remove(user_session.new_messages)


class Server:
    def __init__(self, ip:str = DEFAULT_SERVER_ADDR, port:int = DEFAULT_SERVER_PORT, rooms:List[Room] = list()):
        self.ip = ip
        self.port = port
        self.init_main_socket(ip, port)
        self.open_sockets = {self.main_socket: None}
        self.rooms = dict()
        # Tests
        self._add_test()

    def _add_test(self) -> None:
        """
        Adds default rooms for testing the program.

        @param self: The server to add the rooms to.
        """
        self._add_test_room("kita-alef", ["yuval", "yuval2", "yuval3"], [b"blablabla", b"blablbla2", b"blblbla3"])
        self._add_test_room("kita-bet", ["yuval", "yuval2"], [b"cacacacacac", b"cacacacaca2", b"cacacaca3"])

    def _add_test_room(self, room_name:str, members:List[str], messages:List[bytes]) -> None:
        """
        Adds a room to the server.

        @param self: The server to add the room to.
        @param room_name: The name of the room to add.
        @param members: A list containing the names of all the allowed members.
        @param messages: A list of messages to add to the room.
        """
        new_room = Room(room_name, members, messages)
        self.rooms.update({room_name : new_room})

    def init_main_socket(self, ip:str, port:str) -> None:
        """
        Inits the main socket of the server.

        @param self: The server to init the main socket to.
        @param ip: The IPv4 address for the server to listen on.
        @param port: The port number for the server to listen on.
        """
        self.main_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        self.main_socket.bind((ip, port))

    def accept(self) -> None:
        """
        Accepts a new connection to the server.

        @param self: The server to add accept the new connection on.
        """
        sock, addr = self.main_socket.accept()
        self.open_sockets.update({sock: None})

    def _handle_new_connection(self, client_socket:socket.socket, request:Chat_Request) -> None:
        """
        Attaches a `User_Session` according to the new "connection request" with the socket
        for later communication between the client and the server.

        @param client_socket: The socket which the new connection is comming from.
        @param request: The new connection request that had been sent.
        """ 
        
        # If the user wants to change his room it pops the previous `User_Session` for the new one.
        self.close_user_session(self.open_sockets.get(client_socket, None))

        # The new `User_Session` object.
        decoded_args = [arg.decode() for arg in request.args]
        new_session = User_Session(*decoded_args)
        self.open_sockets.update({client_socket: new_session}) 
        requested_room = self.rooms.get(new_session.room_name, None)
        if requested_room:
            if requested_room.add_subscriber(new_session):
                return
        
        close_request = Close_Request("This Room doesnt exits or you dont have permissions to it!".encode())
        client_socket.send(close_request.encode())
        self.close_client_connection(client_socket)

    def _handle_new_message(self, client_socket:socket.socket, raw_message:bytes) -> None:
        """
        Handles and called when the client wants/sent a new message.

        @param client_socket: The socket which the request came from.
        @param raw_message: The raw message sent by the client.
        """
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            close_request = Close_Request("No connection procedure happend! Try again with doing it properly!".encode())
            client_socket.send(close_request.encode())
            self.close_client_connection(client_socket)
            return
        requested_room = self.rooms.get(user_session.room_name, None)
        if requested_room:
            requested_room.invoke_new_message(raw_message)

    def handle_user_request(self, client_socket:socket.socket) -> None:
        """
        Handles and called every time the client sends a request.
        It then calls the relevent function for each request type.

        @param client_socket: The socket which the request is received from.
        """
        request = client_socket.recv(Chat_Request.MAX_REQUEST_SIZE)
        if not request:
            return
        decoded_request = Chat_Request.decode(request)
        req_type = decoded_request.type
        if req_type == Request_Type.NEW_CONNECTION:
            self._handle_new_connection(client_socket, decoded_request)
        if req_type == Request_Type.MESSAGE:
            self._handle_new_message(client_socket, decoded_request.args[0])
        if req_type == Request_Type.EXIT:
            self.close_client_connection(client_socket)
    
    def close_user_session(self, user_session:User_Session) -> None:
        """
        Closes a `User_Session` object.
        It removes its subscriptions to its room.

        @param user_session: The `User_Session` to close.
        """
        if user_session:
            requested_room = self.rooms.get(user_session.room_name, None)
            if requested_room:
                requested_room.unsubscribe(user_session)

    def close_client_connection(self, client_socket:socket.socket) -> None:
        """
        Closes the connection between this client and the server.
        It is being called everytime we want to `terminate` or stop the communication socket itself.
        It closes the socket and closes the user session (with the `close_user_sesion` function).

        @param client_socket: The socket to close the communication with.
        """
        client_socket.close()
        user_session = self.open_sockets.pop(client_socket, None)
        self.close_user_session(user_session)
    
    def push_to_client(self, client_socket:socket.socket) -> None:
        """
        Pushes/Sends the pending messages to the client.
        
        @param client_socket: The socket to send/push the messages through.
        """
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            return

        if not user_session.new_messages.empty():
            msg = user_session.new_messages.get()
            msg_request = Message_Request(msg)
            client_socket.send(msg_request.encode())

    def _handle_exception(self, client_socket:socket.socket, excep:Exception) -> None:
        """
        Handles exceptions the happend in client_socket.

        @param client_socket: The socket which the exception happend in.
        @param excep: The exception that happend.
        """
        try:
            raise excep
        except ConnectionResetError as e:
            pass
        except BrokenPipeError as e:
            pass
        finally:
            print(excep)
            self.close_client_connection(client_socket)

    def start_server(self) -> None:
        """
        Starts the server and does the main loop of the server
        """
        self.main_socket.listen()
        while True:
            readable, writable, _ = select.select(self.open_sockets,self.open_sockets,[])
            for client_socket in readable:
                if client_socket is self.main_socket:
                    self.accept()
                else:
                    self.handle_user_request(client_socket)
            
            for client_socket in writable:
                try:
                    self.push_to_client(client_socket)
                except Exception as e:
                    self._handle_exception(client_socket, e)
 
        for sock in self.open_sockets:
            sock.close()
        self.main_socket.close()


def init_argparser() -> argparse.ArgumentParser:
    """
    Inits the argparser for the server.
    """
    parser = argparse.ArgumentParser("Chat server")
    parser.add_argument("port", type=int, default=DEFAULT_SERVER_PORT, help="The server's listening port")
    return parser


if __name__ == "__main__":
    parser = init_argparser()
    args = parser.parse_args()
    server = Server(DEFAULT_SERVER_ADDR, args.port)
    server.start_server()
