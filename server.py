import socket
from typing import List, Dict, Optional
import select
from queue import Queue
from chat import Request_Type, Chat_Request, Message_Request, Close_Request
import argparse

DEFAULT_SERVER_ADDR = "0.0.0.0"
DEFAULT_SERVER_PORT = 33333

class Room_Permissions:
    def __init__(self, close_room:bool=False, kick:bool=False, lock_room:bool=False):
        self.close_room = close_room
        self.kick = kick
        self.lock_room = lock_room

class Server_Permissions:
    def __init__(self, shutdown:bool=False, whisper:bool=True, multicast:bool=True):
        self.shutdown = shutdown
        self.whisper = whisper
        self.mulitcast = multicast


class Admin_Server_Permissions(Server_Permissions):
    def __init__(self, shutdown:bool=True, whisper:bool=True, multicast:bool=True):
        super().__init__(shutdown=shutdown, whisper=whisper, multicast=multicast)


class User_Session:
    """
    Holds additional information about the user session so that the server would know
    what each socket needs to get and to where he needs to send.
    """
    def __init__(self, client_socket:socket.socket, username:str = None, room_name:str = None,
                    permissions:Server_Permissions=Server_Permissions()):
        self.username = username
        self.room_name = room_name
        self.new_messages = Queue()
        self.client_socket = client_socket
        self.permissions = permissions
        self.is_active = True
    
    def close_connection(self, raw_message:bytes):
        self.is_active = False
        close_request = Close_Request(raw_message)
        self.client_socket.send(close_request.encode()) 
        self.client_socket.close()


class Room:
    def __init__(self, name:str, members:Dict[str, Optional[Room_Permissions]] = list(), messages:List[bytes] = list(),
                    locked:bool=False, default_permissions:Room_Permissions=Room_Permissions()):
        self.name = name
        self.messages = messages

        # All the members in the room, how can enter and whats their permissions.
        self._members = members
        
        # Current active `User_Sessions`s to receive and send messages.
        self.subscribers = list() 
        
        self.locked = locked
        self.default_permissions = default_permissions

    def _can_subscribe(self, user_session:User_Session) -> bool:
        if not self.locked:
            return True
        if user_session.username in self._members.keys():
            return True
        return False

    def add_subscriber(self, user_session:User_Session) -> bool:
        """
        Adds a `User_Session` as a subscriber to be invoked when a new message is added.
        Returns indication if the operation was successful.

        @param user_session: The `User_Session` to subscribe.
        @return: A bool indication if the operation was succesful or not.
        """
        if not self._can_subscribe(user_session):
            return False
        
        self.subscribers.append(user_session)
        if user_session.username not in self._members.keys():
            self._members.update({user_session.username:self.default_permissions})
        return True

    def invoke_new_message(self, raw_message:bytes) -> None:
        """
        Add the new message to all of the subscribers.
        
        @param raw_message: The message to add.
        """
        self.messages.append(raw_message)
        for user_session in self.subscribers:
            if user_session.is_active:
                try:
                    user_session.new_messages.put(raw_message)
                except Exception as e:
                    self.subscribers.remove(user_session)
            else:
                self.unsubscribe(user_session)

    def unsubscribe(self, user_session:User_Session) -> None:
        """
        Removes the given `User_Session` from the subscribers.

        @param user_session: The `User_Session` to remove from the subscribers.
        """
        if user_session in self.subscribers:
            self.subscribers.remove(user_session)
    
    def _get_user_permissions(self, username:str) -> Optional[Room_Permissions]:
        if username not in self._members.keys():
            return None
        user_perms = self._members.get(username, None)
        if user_perms is None:
            user_perms = self.default_permissions
        return user_perms

    def lock(self, user_session:User_Session) -> bool:
        user_perms = self._get_user_permissions(user_session.username)
        if user_perms is None:
            return False
        if user_perms.lock_room:
            self.locked = not self.locked
        return True

    def close(self, user_session:User_Session) -> bool:
        user_perms = self._get_user_permissions(user_session.username)
        if user_perms is None:
            return False
        return user_perms.close_room

    def fill_history(self, user_session:User_Session) -> bool:
        if not self._can_subscribe(user_session):
            return False
        for raw_message in self.messages:
            user_session.new_messages.put(raw_message)
        return True
    
    def kick(self, user_session:User_Session, username:str) -> List[User_Session]:
        sessions_kicked = []
        user_perms = self._get_user_permissions(user_session.username)
        if user_perms is None:
            return sessions_kicked
        if not user_perms.kick:
            return sessions_kicked
        self._members.pop(username)
        
        for member_session in self.subscribers:
            if member_session.username == username:
                sessions_kicked.append(member_session)
        return sessions_kicked
        

class Server:
    def __init__(self, ip:str = DEFAULT_SERVER_ADDR, port:int = DEFAULT_SERVER_PORT, rooms:List[Room] = list(),
                    admins:List[str] = list()):
        self.ip = ip
        self.port = port
        self.admins = admins
        self.rooms = dict()
        self._init_request_handler()
        self.init_main_socket(ip, port)
        self.open_sockets = {self.main_socket: None}
        # Tests
        self._add_test()

    def _add_test(self) -> None:
        """
        Adds default rooms for testing the program.

        @param self: The server to add the rooms to.
        """
        self._add_test_room("kita-alef", {"yuval":Room_Permissions(True, True, True), "yuval2":None, "yuval3":None}, [b"blablabla", b"blablbla2", b"blblbla3"])
        self._add_test_room("kita-bet", {"yuval":None, "yuval2":None}, [b"cacacacacac", b"cacacacaca2", b"cacacaca3"])
        self.admins = ["yuval"]

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

    def _init_request_handler(self) -> None:
        """
        Inits the request handler dict which is responsible for handling the different requests.
        """
        self.request_handler = dict()
        self.request_handler[Request_Type.NEW_CONNECTION] = self._handle_new_connection
        self.request_handler[Request_Type.MESSAGE] = self._handle_new_message
        self.request_handler[Request_Type.EXIT] = self.close_client_connection
        self.request_handler[Request_Type.LOCK_ROOM] = self._handle_lock_room
        self.request_handler[Request_Type.HISTORY] = self._handle_history
        self.request_handler[Request_Type.SHUTDOWN] = self._handle_shutdown
        self.request_handler[Request_Type.CLOSE_ROOM] = self._handle_close_room
        self.request_handler[Request_Type.KICK] = self._handle_kick_user
        # self.request_handler[Request_Type.


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

    def _handle_new_connection(self, client_socket:socket.socket, *request_args) -> None:
        """
        Attaches a `User_Session` according to the new "connection request" with the socket
        for later communication between the client and the server.

        @param client_socket: The socket which the new connection is comming from.
        @param request_args: The arguments of the request that had been sent.
        """ 
        
        # If the user wants to change his room it pops the previous `User_Session` for the new one.
        self.close_user_session(self.open_sockets.get(client_socket, None))

        # The new `User_Session` object.
        decoded_args = [arg.decode() for arg in request_args]
        username, room_name = decoded_args

        if username in self.admins:
            user_perms = Admin_Server_Permissions()
        else:
            user_perms = Server_Permissions()

        new_session = User_Session(client_socket, username, room_name, user_perms)
        self.open_sockets.update({client_socket: new_session}) 
        requested_room = self.rooms.get(new_session.room_name, None)
        if requested_room:
            if requested_room.add_subscriber(new_session):
                return
        
        close_request = Close_Request("This Room doesnt exits or you dont have permissions to it!".encode())
        client_socket.send(close_request.encode())
        self.close_client_connection(client_socket)

    def _handle_new_message(self, client_socket:socket.socket, raw_message:bytes, *args) -> None:
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

    def _handle_kick_user(self, client_socket:socket.socket, user_to_kick:bytes,
                            raw_kick_message:bytes=b"", *args) -> None:
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            # need to make it throw exception!
            pass
            return
        requested_room = self.rooms.get(user_session.room_name, None)
        if not requested_room:
            # need to make it throw exception!
            pass
            return
        kicked_users = requested_room.kick(user_session, user_to_kick.decode())
        for user in kicked_users:
            self.open_sockets.pop(user.client_socket)
            user.close_connection(raw_kick_message)

    def _handle_lock_room(self, client_socket:socket.socket, room_name_encoded:bytes=None, *args):
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            # need to make it throw exception!
            pass
            return
        
        request_room_name = user_session.room_name
        if room_name_encoded:
            request_room_name = room_name_encoded.decode()
        requested_room = self.rooms.get(request_room_name, None)
        if not requested_room:
            # need to make it throw exception!
            pass
            return
        requested_room.lock(user_session)

    def _handle_history(self, client_socket:socket.socket, room_name_encoded:bytes=None, *args):
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            # need to make it throw exception!
            pass
            return
        
        request_room_name = user_session.room_name
        if room_name_encoded:
            request_room_name = room_name_encoded.decode()
        requested_room = self.rooms.get(request_room_name, None)
        if not requested_room:
            # need to make it throw exception!
            pass
        requested_room.fill_history(user_session)

    def _handle_shutdown(self, client_socket:socket.socket, raw_message:bytes=b"", *args):
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            # need to make it throw exception!
            pass
            return
        if not user_session.permissions.shutdown:
            # need to make it throw exception!
            pass
            return
        self.open_sockets.pop(self.main_socket)
        self.main_socket.close()
        for sock, user_session in self.open_sockets.items():
            user_session.close_connection(raw_message)
        
        self.open_sockets.clear()
    
    def _handle_close_room(self, client_socket:socket.socket, room_name_encoded:bytes=b"",
                            raw_message:bytes=b"", *args):
        user_session = self.open_sockets.get(client_socket, None)
        if user_session is None:
            # need to make it throw exception!
            pass
            return
        
        request_room_name = user_session.room_name
        if room_name_encoded:
            request_room_name = room_name_encoded.decode()
        requested_room = self.rooms.get(request_room_name, None)
        if not requested_room:
            # need to make it throw exception!
            pass
        if requested_room.close(user_session):
            for user_session in requested_room.subscribers:
                self.open_sockets.pop(user_session.client_socket)
                user_session.close_connection(raw_message)
            self.rooms.pop(requested_room.name)


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

        handler = self.request_handler.get(req_type, None)
        if handler is None:
            return

        handler(client_socket, *decoded_request.args)

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

    def close_client_connection(self, client_socket:socket.socket, *args) -> None:
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
        while len(self.open_sockets) > 0:
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
