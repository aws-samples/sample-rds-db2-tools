import java.sql.*;
import java.util.Properties;

public class Db2SSLTest {
  public static void main(String[] args) {
    if (args.length != 6) {
      System.out.println("Usage: java Db2SSLTest " +
        " <certchain.pem> " +
        "  <hostname> <port> <database> <userid> <password>");
      System.exit(1);
    }

    Properties properties = new Properties();
    String certPath = args[0];
    String hostname = args[1];
    String port = args[2];
    String database = args[3];
    String userid = args[4];
    String password = args[5];

    properties.put("sslConnection", "true");
    properties.put("sslVersion", "TLSv1.2");
    properties.put("sslCertLocation", certPath);
    properties.put("user", userid);
    properties.put("password", password); 

    String url = "jdbc:db2://" + hostname + ":" + 
        port + "/" + database;

    try {
      Class.forName("com.ibm.db2.jcc.DB2Driver");
      Connection conn = DriverManager.getConnection(url, 
                          properties);

      Statement stmt = conn.createStatement();
      ResultSet rs = stmt.executeQuery("SELECT CURRENT " +
          " TIMESTAMP " +
          " FROM SYSIBM.SYSDUMMY1");

      if (rs.next()) {
        System.out.println("SSL Connection successful!");
        System.out.println("Current timestamp: " + 
           rs.getString(1));
      }

      rs.close();
      stmt.close();
      conn.close();

    } catch (Exception e) {
      System.err.println("Error: " + e.getMessage());
      e.printStackTrace();
    }
  }
}