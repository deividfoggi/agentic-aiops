from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
import asyncio
from pydantic import BaseModel
from typing import Any, List
from utils.agents import Agents
from tools.queryazmonitor import query_azure_monitor
from datetime import timedelta
import json
from utils.logger import setup_logger
from fastapi.middleware.cors import CORSMiddleware

# Set up logging
logger = setup_logger(__name__)

class CustomConsoleHandler:
    """Custom handler that captures agent messages and sends them to WebSocket."""
    
    def __init__(self, websocket):
        self.websocket = websocket
        
    async def __call__(self, message):
        try:
            # Handle unexpected message formats
            await self.websocket.send_json({
                "sender": message.source,
                "text": message.content
            })
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

class APIEndpoint:
    """
    A class to define and manage a FastAPI application with specific routes and background task handling.
    """
    def __init__(self):
        self.app = FastAPI()
        self.background_tasks = set()
        self.active_connections: List[WebSocket] = []
        
        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3000"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        self.setup_routes()

    def setup_routes(self):
        @self.app.post("/alert")
        async def process_payload(request: Request):
            try:
                # Log the incoming request
                logger.info(f"Received request from {request.client.host} with headers: {request.headers}")
                
                # Read the request body
                payload = await request.json()

                # Convert the JSON payload to a string
                event_str = json.dumps(payload)
                
                # Initialize the Agents class instance
                agents = Agents()
                
                # Create an asynchronous task to run the agent's task with the event string
                task = asyncio.create_task(agents.run_task(event_str))
                
                # Add the created task to the set of background tasks
                self.background_tasks.add(task)
                
                # Ensure the task is removed from the set once it is completed
                task.add_done_callback(self.background_tasks.discard)
                
                # Return a success response with HTTP status 200
                return {"status": "success"}, 200

            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing payload: {str(e)}"
                )
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            logger.info(f"WebSocket connection established: {websocket.client}")

            try:
                agents = Agents()

                # Use the CustomConsoleHandler to send messages to the WebSocket
                console_handler = CustomConsoleHandler(websocket)

                while True:
                    # Receive a message from the WebSocket client
                    data = await websocket.receive_text()
                    data_json = json.loads(data)
                    logger.info(f"Received WebSocket message: {data}")

                    if "event" in data_json:
                        event = data_json["event"]

                        try:
                            # Run the agent's task and stream responses to the WebSocket
                            await agents.run_task(event, stream_handler=console_handler)
                            
                            # Send a success message when the task is complete
                            await websocket.send_json({"status": "success", "message": "Task completed"})
                        except Exception as e:
                            logger.error(f"Error processing event: {str(e)}")
                            await websocket.send_json({"status": "error", "error": str(e)})

            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {websocket.client}")
            except Exception as e:
                logger.error(f"WebSocket error: {str(e)}", exc_info=True)
            finally:
                logger.info(f"Cleaning up WebSocket connection: {websocket.client}")
        
        @self.app.post("/run_task")
        async def run_task(request: Request):
            try:
                # Parse the request body
                body = await request.json()
                event = body.get("event", "")
                
                if not event:
                    raise HTTPException(status_code=400, detail="Missing 'event' parameter")
                
                # Initialize the Agents class
                agents = Agents()
                
                # Execute the task and get the response
                response = await agents.team.run(task=event)
                
                # Return the response
                return {"response": response}
            except Exception as e:
                logger.error(f"Error in run_task: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing task: {str(e)}"
                )
    
    # Method to return the FastAPI application instance
    def get_app(self):
        return self.app
    
# Create an instance of the APIEndpoint class
api = APIEndpoint()

# Retrieve the FastAPI application instance from the APIEndpoint instance
app = api.get_app()

# Define an event handler for the "startup" event
@app.on_event("startup")
async def startup_event():
    # Placeholder for any startup logic (currently does nothing)
    pass

# Define an event handler for the "shutdown" event
@app.on_event("shutdown")
async def shutdown_event():
    # Check if there are any background tasks still running
    if api.background_tasks:
        # Wait for all background tasks to complete before shutting down
        await asyncio.gather(*api.background_tasks)