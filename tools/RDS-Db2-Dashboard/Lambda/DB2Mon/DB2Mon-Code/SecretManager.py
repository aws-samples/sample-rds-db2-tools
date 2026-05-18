import json
from botocore.exceptions import ClientError
import base64

class SecretManager:
    def __init__(self, logger, secret_name, boto3_sm_client):
        """
        Initialize the SecretManager with the secret name.
        :param secret_name: The name of the secret stored in AWS Secrets Manager.
        """
        self.client = boto3_sm_client
        self.secret_name = secret_name
        self.logger = logger
        
    def get_secret(self):
        """
        Retrieve the secret from AWS Secrets Manager.
        :return: Dictionary of key-value pairs from the secret.
        """
        try:
            response = self.client.get_secret_value(SecretId=self.secret_name)
            if 'SecretString' in response:
                secret = response['SecretString']
                self.logger.info(f"Retrieved secret for {self.secret_name}.")
                data = json.loads(secret)
                master_arn = data.get('masterSecretArn')
                if master_arn and master_arn != 'None':
                    try:
                        master = self.client.get_secret_value(SecretId=master_arn)
                        data['password'] = json.loads(master['SecretString']).get('password', data.get('password'))
                        self.logger.info("Password resolved from RDS-managed secret.")
                    except ClientError as me:
                        # Stale masterSecretArn (e.g. DB flipped to self-managed, or rotated).
                        # Fall back to the embedded 'password' field instead of failing.
                        code = me.response.get('Error', {}).get('Code', '')
                        if code in ('ResourceNotFoundException', 'InvalidRequestException',
                                    'AccessDeniedException', 'DecryptionFailureException'):
                            if data.get('password'):
                                self.logger.warning(
                                    f"masterSecretArn lookup failed ({code}); "
                                    f"using self-managed password from {self.secret_name}."
                                )
                            else:
                                self.logger.error(
                                    f"masterSecretArn lookup failed ({code}) and no fallback "
                                    f"'password' field present in {self.secret_name}."
                                )
                                raise
                        else:
                            raise
                else:
                    self.logger.info("Using self-managed password from secret (no masterSecretArn).")
                return data
            else:
                secret = base64.b64decode(response['SecretBinary'])
                self.logger.error("SecretString not found in response.")
                return {}
        except ClientError as e:
            if e.response['Error']['Code'] == 'DecryptionFailureException':
                raise e
            elif e.response['Error']['Code'] == 'InternalServiceErrorException':
                raise e
            elif e.response['Error']['Code'] == 'InvalidParameterException':
                raise e
            elif e.response['Error']['Code'] == 'InvalidRequestException':
                raise e
            elif e.response['Error']['Code'] == 'ResourceNotFoundException':
                raise e
            else:
                raise e