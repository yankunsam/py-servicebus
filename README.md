# Service Bus use RabbitMQ

Provide a basic RPC and Message process framework use RabbitMQ message server.

Features:
	1. Auto reconnect when network is down
	2. Support SSL and dynamic token validation in message transfer
	3. Use multi-path to auto switch RabbitMQ server when major RabbitMQ server is down

## Archtecture

						   +---------+
						   | Message |
						   | Sender  |
						   +---------+
						   		|
					  +---------+--------+
					  |  				 |
					  V  				 V
				+----------+		+----------+
				| RabbitMQ |		| RabbitMQ |
				|  Master  |		|  Slave   |
				+----------+		+----------+
					  ^					  ^
					  |					  |
			  +------------------+-------------------+
			  | Queue A			 | Queue B			 | Queue C
		+---------+			+---------+			+---------+
		|  Agent  |			|  Agent  |			|  Agent  |
		| Server  |			| Server  |			| Server  |
		+---------+			+---------+			+---------+

### Message Sender

Message Sender will send the message to Agent Server. Sender use specific Queue Name and Service Name to determin this message is send to which Agent Server's Service
Message Sender can have many RabbitMQ server's host when Message Sender need to connect to RabbitMQ server, it will use first connectable server. If none of then can connect it will raise an exception.

### Agent Server

Agent Server will connect to RabbitMQ server and listen a Queue to take messages. In this framework, Agent Server will be fork N process (N is number of RabbitMQ servers) to listen each RabbitMQ server's Queue, so that can handle when one RabbitMQ server is down, Agent can take messages from Other RabbitMQ server.

### Service

Service is business logic code. There will be two type of service: RPC Service and Message Service.
RPC Service will get message process process it then response a result message to sender provided tempory Queue.
Message Service will get message and process it but no need to send a result message to sender.
Each Service has two params to name it: category and name. When we use Sender to call this service you can use: NODE_NAME.CATEGORY.NAME to address the service.

## Message Format

There has two type of message: Call Message and Response Message
Call Message:

		<?xml version="1.0"?>
		<event>
			<id>EVENT_ID</id>
			<token>EVENT_TOKEN</token>
			<category>SERVICE_CATEGORY</category>
			<service>SERVICE_NAME</service>
			<params>JSON_FORMAT_PARAMS</params>
		</event>

Response Message:

		<?xml version="1.0"?>
		<response>
			<id>EVENT_ID</id>
			<message>JSON_FORMAT_MESSAGE</message>
		</response>

## Usage

### Install

		$ python setup.py install

### Agent part

Write a Service:

		class AddService:
			def on_call(self, request, response):
				params = request.get_params()
				ret = 0
				for i in params:
					ret += int(i)
				response.send(ret)

Then regist it to ServiceBus and run it:

		from servicebus.service import ServiceBus
		from servicebus.configuration import Configuration

		config = Configuration({
			'hosts': ['localhost'],
			'user': 'admin',
			'password': '123456',
			'use_ssl': False,
			'node_name': "NODE-01",
			'secret_token': 'secret token',
		})
		sbus = ServiceBus(config)
		sbus.add_rpc_service("math", "add", AddService())
		sbus.run_services()

### Call part

If we want to call NODE-01's math.add service, the code should be:

		from servicebus.configuration import Configuration
		from servicebus.sender import Sender

		config = Configuration({
			'hosts': ['localhost'],
			'user': 'admin',
			'password': '123456',
			'use_ssl': False,
			'node_name': "NODE-01",
			'secret_token': 'secret token',
		})
		sender = Sender(config)
		ret = sender.call('NODE-01.math.add', [1, 2])
		print ret

Then ret will be (1, 3). Sender#call will return a tuple, it contains 2 items first is Event ID second is result that Service return.